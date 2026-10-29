from datetime import datetime, time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Cliente, Habitacion, Reserva, Usuario


class HomeTests(TestCase):
    def setUp(self):
        self.habitacion = Habitacion.objects.create(numero=101, tipo='NORMAL', precio_base_noche=Decimal('45000'))
        self.entrada = timezone.localdate() + timedelta(days=10)
        self.salida = self.entrada + timedelta(days=2)

    def test_inicio_y_recursos_locales(self):
        respuesta = self.client.get(reverse('hotel:home'))
        self.assertContains(respuesta, 'El sur se vive.')
        self.assertContains(respuesta, '/static/hotel/css/home.css')
        self.assertContains(respuesta, 'llanquihue-hero.webp')
        self.assertContains(respuesta, 'Normal')

    def bloquear(self, **extra):
        datos = dict(
            habitacion=self.habitacion,
            fecha_entrada=timezone.make_aware(datetime.combine(self.entrada, time(14))),
            fecha_salida=timezone.make_aware(datetime.combine(self.salida, time(12))),
            estado='CONFIRMADA',
        )
        datos.update(extra)
        return Reserva.objects.create(**datos)

    def buscar(self, **extra):
        datos = dict(entrada=self.entrada.isoformat(), salida=self.salida.isoformat(), huespedes='2')
        datos.update(extra)
        return self.client.get(reverse('hotel:home'), datos)

    def test_disponibilidad_excluye_reservas_y_mantenimiento(self):
        self.bloquear()
        Habitacion.objects.create(numero=102, tipo='GRANDE', precio_base_noche=Decimal('80000'), estado_actual='MANTENIMIENTO')
        respuesta = self.buscar()
        self.assertContains(respuesta, 'No encontramos habitaciones')
        self.assertEqual(respuesta.context['categorias'], [])

    def test_retencion_vencida_no_bloquea_pero_activa_si(self):
        reserva = self.bloquear(estado='RETENIDA', retencion_hasta=timezone.now() - timedelta(minutes=1))
        self.assertEqual(len(self.buscar().context['categorias']), 1)
        reserva.retencion_hasta = timezone.now() + timedelta(minutes=5)
        reserva.save(update_fields=['retencion_hasta'])
        self.assertEqual(self.buscar().context['categorias'], [])

    def test_fechas_invalidas_se_muestran_como_error(self):
        respuesta = self.buscar(salida=self.entrada.isoformat())
        self.assertContains(respuesta, 'La salida debe ser posterior')
        self.assertFalse(respuesta.context['busqueda_valida'])

    def test_registro_crea_cliente_y_sesion_sin_permisos_administrativos(self):
        respuesta = self.client.post(reverse('hotel:registro'), {
            'nombre': 'Ana', 'apellido': 'Perez', 'rut': '11111111-1',
            'email': 'ana@example.com', 'telefono': '912345678',
            'password1': 'Escapada-Sur-2864!', 'password2': 'Escapada-Sur-2864!', 'acepta_condiciones': 'on',
        })
        self.assertRedirects(respuesta, reverse('hotel:fechas'))
        usuario = Usuario.objects.get(email='ana@example.com')
        self.assertTrue(Cliente.objects.filter(usuario=usuario).exists())
        self.assertTrue(usuario.check_password('Escapada-Sur-2864!'))
        self.assertFalse(usuario.is_staff)
        self.assertEqual(int(self.client.session['_auth_user_id']), usuario.pk)

    def test_login_correo_logout_post_y_admin_siguen_disponibles(self):
        usuario = Usuario.objects.create_user(email='ana@example.com', password='Escapada-Sur-2864!', rut='11111111-1', nombre='Ana', apellido='Perez', telefono='912345678')
        respuesta = self.client.post(reverse('hotel:ingresar'), {'username': usuario.email, 'password': 'Escapada-Sur-2864!'})
        self.assertRedirects(respuesta, reverse('hotel:home'))
        self.assertContains(self.client.get(reverse('hotel:home')), 'Ana · Mi cuenta')
        self.assertRedirects(self.client.post(reverse('hotel:salir')), reverse('hotel:home'))
        self.assertEqual(self.client.get('/admin/').status_code, 302)
