from datetime import datetime, time, timedelta
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import Habitacion, Reserva, ServicioAdicional, Usuario


class PantallasReservaTests(TestCase):
    def setUp(self):
        self.entrada = timezone.localdate() + timedelta(days=7)
        self.salida = self.entrada + timedelta(days=2)
        self.datos = {'entrada': self.entrada.isoformat(), 'salida': self.salida.isoformat(), 'huespedes': '2'}
        self.habitacion = Habitacion.objects.create(numero=101, tipo='NORMAL', precio_base_noche=Decimal('20000'))
        self.extra = ServicioAdicional.objects.create(nombre='Desayuno', precio=Decimal('5000'))

    def test_tres_pantallas_se_renderizan(self):
        for nombre in ('registro', 'fechas', 'habitaciones'):
            respuesta = self.client.get(reverse(f'hotel:{nombre}'), self.datos if nombre == 'habitaciones' else {})
            self.assertEqual(respuesta.status_code, 200)
        self.assertContains(self.client.get(reverse('hotel:fechas')), 'calendarios')

    def test_resultados_filtran_y_aceptan_salida_igual_a_nueva_entrada(self):
        reserva = Reserva.objects.create(
            habitacion=self.habitacion, estado='CONFIRMADA',
            fecha_entrada=timezone.make_aware(datetime.combine(self.entrada, time(14))),
            fecha_salida=timezone.make_aware(datetime.combine(self.salida, time(12))),
        )
        respuesta = self.client.get(reverse('hotel:habitaciones'), self.datos)
        self.assertContains(respuesta, 'No hay habitaciones disponibles')
        siguientes = dict(self.datos, entrada=self.salida.isoformat(), salida=(self.salida + timedelta(days=1)).isoformat())
        self.assertEqual(len(self.client.get(reverse('hotel:habitaciones'), siguientes).context['habitaciones']), 1)

    def test_fechas_invalidas_redirigen_al_calendario(self):
        respuesta = self.client.get(reverse('hotel:habitaciones'), dict(self.datos, salida=self.entrada.isoformat()))
        self.assertEqual(respuesta.status_code, 302)
        respuesta = self.client.get(respuesta.url)
        self.assertContains(respuesta, 'La salida debe ser posterior')

    def test_seleccion_guarda_ids_y_cantidades_sin_crear_reserva(self):
        datos = dict(self.datos, habitacion=str(self.habitacion.pk), **{f'extra_{self.extra.pk}': '2'})
        respuesta = self.client.post(reverse('hotel:seleccionar'), datos)
        self.assertIn(reverse('hotel:registro'), respuesta.url)
        seleccion = self.client.session['seleccion_estadia']
        self.assertEqual(seleccion['habitacion'], self.habitacion.pk)
        self.assertEqual(seleccion['extras'], {str(self.extra.pk): 2})
        self.assertFalse(Reserva.objects.exists())
        self.assertNotIn('precio', seleccion)

    def test_no_acepta_habitacion_inexistente_ni_cantidad_invalida(self):
        respuesta = self.client.post(reverse('hotel:seleccionar'), dict(self.datos, habitacion='999999'))
        self.assertIn(reverse('hotel:habitaciones'), respuesta.url)
        respuesta = self.client.post(reverse('hotel:seleccionar'), dict(self.datos, habitacion=str(self.habitacion.pk), **{f'extra_{self.extra.pk}': '-1'}))
        self.assertIn(reverse('hotel:habitaciones'), respuesta.url)
        self.assertNotIn('seleccion_estadia', self.client.session)

    def test_registro_respeta_continuacion_y_rechaza_redirect_externo(self):
        datos = {'nombre': 'Ana', 'apellido': 'Perez', 'rut': '11111111-1', 'email': 'ana@example.com', 'telefono': '912345678', 'password1': 'Escapada-Sur-2864!', 'password2': 'Escapada-Sur-2864!', 'acepta_condiciones': 'on', 'next': 'https://example.org/'}
        respuesta = self.client.post(reverse('hotel:registro'), datos)
        self.assertRedirects(respuesta, reverse('hotel:fechas'))
        self.assertFalse(Usuario.objects.get(email='ana@example.com').is_staff)
