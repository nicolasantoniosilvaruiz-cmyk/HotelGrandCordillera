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
        datos = {'nombre': 'Ana', 'apellido': 'Perez', 'rut': '11111111-1', 'email': 'ana@example.com', 'telefono_pais': '+56', 'telefono_numero': '12345678', 'password1': 'Escapada-Sur-2864!', 'password2': 'Escapada-Sur-2864!', 'acepta_condiciones': 'on', 'next': 'https://example.org/'}
        respuesta = self.client.post(reverse('hotel:registro'), datos)
        self.assertRedirects(respuesta, reverse('hotel:fechas'))
        self.assertFalse(Usuario.objects.get(email='ana@example.com').is_staff)

    def test_registro_desde_pago_aplica_y_muestra_bienvenida_una_vez(self):
        seleccion = dict(self.datos, habitacion=str(self.habitacion.pk), **{f'extra_{self.extra.pk}': '2'})
        self.client.post(reverse('hotel:seleccionar'), seleccion)
        pago = reverse('hotel:pago')
        invitado = self.client.get(pago)
        self.assertContains(invitado, 'Estás reservando como invitado')
        retencion = invitado.context['reserva']
        registro = self.client.post(reverse('hotel:registro'), {
            'nombre': 'Ana', 'apellido': 'Perez', 'rut': '11111111-1',
            'email': 'ana@example.com', 'telefono_pais': '+56', 'telefono_numero': '12345678',
            'password1': 'Escapada-Sur-2864!', 'password2': 'Escapada-Sur-2864!',
            'acepta_condiciones': 'on', 'next': pago,
        })
        self.assertRedirects(registro, pago)
        habitaciones = reverse('hotel:habitaciones')
        self.assertContains(self.client.get(habitaciones, self.datos), 'Bienvenida · 5% en alojamiento')
        resumen = self.client.get(pago)
        self.assertEqual(resumen.context['reserva'].pk, retencion.pk)
        self.assertContains(resumen, 'Bienvenida · 5% en alojamiento')
        confirmacion = self.client.post(pago, {
            'revision': resumen.context['form'].initial['revision'],
            'resultado_pago': 'APROBADO', 'acepta_condiciones': 'on',
        }, follow=True)
        self.assertContains(confirmacion, 'Bienvenida · 5% en alojamiento')
        retencion.refresh_from_db()
        self.assertEqual(retencion.cliente.usuario.email, 'ana@example.com')
        self.assertEqual(retencion.descuento_aplicado, Decimal('2000'))
        self.assertEqual(retencion.monto_total, Decimal('48000'))
        self.assertContains(self.client.get(habitaciones, self.datos), 'No hay habitaciones disponibles')
        fechas_nuevas = dict(
            self.datos, entrada=self.salida.isoformat(), salida=(self.salida + timedelta(days=2)).isoformat(),
        )
        self.assertNotContains(self.client.get(habitaciones, fechas_nuevas), 'Bienvenida · 5% en alojamiento')

    def test_capacidad_camas_y_catalogo_por_habitacion(self):
        mediana = Habitacion.objects.create(numero=201, tipo='MEDIANA', precio_base_noche=60000)
        suite = Habitacion.objects.create(numero=301, tipo='SUITE_MED', precio_base_noche=90000)
        grande = Habitacion.objects.create(numero=401, tipo='GRANDE', precio_base_noche=120000)
        self.assertEqual((self.habitacion.capacidad, self.habitacion.cantidad_camas), (2, 1))
        self.assertEqual((mediana.capacidad, mediana.cantidad_camas), (4, 2))
        self.assertEqual((suite.capacidad, suite.cantidad_camas, suite.tipo_cama), (2, 1, 'King'))
        self.assertEqual((grande.capacidad, grande.cantidad_camas, grande.tipo_cama), (4, 2, 'King'))
        respuesta = self.client.get(reverse('hotel:habitaciones'), dict(self.datos, huespedes='4'))
        self.assertEqual({h.pk for h in respuesta.context['habitaciones']}, {mediana.pk, grande.pk})
        self.assertContains(respuesta, '2 camas Matrimonial')
        self.assertContains(respuesta, '2 camas King')
        premium = ServicioAdicional.objects.create(nombre='Masaje privado', categoria='SERVICIO', tipo_minimo='SUITE_MED', precio=10000)
        respuesta = self.client.get(reverse('hotel:habitaciones'), self.datos)
        self.assertContains(respuesta, 'Masaje privado')
        self.assertContains(respuesta, 'data-nivel-minimo="2"')
        self.client.post(reverse('hotel:seleccionar'), dict(self.datos, habitacion=str(self.habitacion.pk), **{f'extra_{premium.pk}':'1'}))
        self.assertNotIn('seleccion_estadia', self.client.session)
        self.client.post(reverse('hotel:seleccionar'), dict(self.datos, habitacion=str(suite.pk), **{f'extra_{premium.pk}':'1'}))
        self.assertEqual(self.client.session['seleccion_estadia']['extras'], {str(premium.pk):1})
