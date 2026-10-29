from datetime import timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import OperationalError, connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from .models import Cliente, DetalleReserva, Habitacion, Reserva, ServicioAdicional, Usuario
from .reservas import ErrorReserva, preparar_pago


class CheckoutTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(email='ana@example.com', rut='11111111-1', nombre='Ana', apellido='Perez', telefono='912345678')
        self.cliente_hotel = Cliente.objects.create(usuario=self.usuario)
        self.habitacion = Habitacion.objects.create(numero=101, tipo='NORMAL', precio_base_noche=Decimal('20000'))
        self.servicio = ServicioAdicional.objects.create(nombre='Desayuno', precio=Decimal('5000'))
        self.seleccion = {
            'entrada': (timezone.localdate()+timedelta(days=7)).isoformat(),
            'salida': (timezone.localdate()+timedelta(days=9)).isoformat(),
            'huespedes': 2, 'habitacion': self.habitacion.pk,
            'extras': {str(self.servicio.pk): 2},
        }

    def preparar(self, registrado=True, cliente=None, seleccion=None):
        cliente = cliente or self.client
        if registrado:
            cliente.force_login(self.usuario)
        sesion = cliente.session
        sesion['seleccion_estadia'] = seleccion or self.seleccion
        sesion.save()
        respuesta = cliente.get(reverse('hotel:pago'))
        self.assertEqual(respuesta.status_code, 200)
        return respuesta

    def datos_pago(self, respuesta, **extra):
        datos = {k: v for k,v in respuesta.context['form'].initial.items()}
        datos.update({
            'titular_nombre': 'Invitada', 'titular_apellido': 'Sur',
            'titular_rut': '22222222-2', 'titular_correo': 'invitada@example.com',
            'titular_telefono': '998765432', 'resultado_pago': 'APROBADO',
            'acepta_condiciones': 'on',
        })
        datos.update(extra)
        return datos

    def test_retencion_precio_descuento_solo_alojamiento_y_no_renueva_plazo(self):
        respuesta = self.preparar()
        reserva = respuesta.context['reserva']
        self.assertEqual(reserva.estado, 'RETENIDA')
        self.assertEqual(reserva.descuento_aplicado, Decimal('2000'))
        self.assertEqual(reserva.monto_total, Decimal('48000'))
        self.assertEqual(reserva.numero_huespedes, 2)
        plazo = reserva.retencion_hasta
        self.client.get(reverse('hotel:pago'))
        reserva.refresh_from_db()
        self.assertEqual(reserva.retencion_hasta, plazo)
        self.assertEqual(Reserva.objects.count(), 1)

    def test_confirma_en_bd_con_detalles_datos_de_relaciones_y_comprobante(self):
        respuesta = self.preparar()
        respuesta = self.client.post(reverse('hotel:pago'), self.datos_pago(respuesta, monto_total='1'))
        reserva = Reserva.objects.get()
        self.assertRedirects(respuesta, reverse('hotel:confirmacion', args=[reserva.codigo]))
        self.assertEqual(reserva.estado, 'CONFIRMADA')
        self.assertTrue(reserva.pago_simulado)
        self.assertIsNotNone(reserva.fecha_confirmacion)
        self.assertIsNone(reserva.retencion_hasta)
        self.assertEqual(reserva.titular_nombre, 'Ana')
        self.assertEqual(reserva.monto_total, Decimal('48000'))
        self.assertEqual(reserva.detalles.get().cantidad, 2)
        recibo = self.client.get(reverse('hotel:comprobante', args=[reserva.codigo]))
        self.assertContains(recibo, 'PAGO DE PRUEBA APROBADO')
        self.assertContains(recibo, reserva.codigo)

    def test_invitado_confirma_sin_cuenta_y_no_recibe_descuento(self):
        respuesta = self.preparar(registrado=False)
        self.assertEqual(respuesta.context['reserva'].monto_total, Decimal('50000'))
        respuesta = self.client.post(reverse('hotel:pago'), self.datos_pago(respuesta))
        reserva = Reserva.objects.get()
        self.assertIsNone(reserva.cliente_id)
        self.assertEqual(reserva.titular_nombre, 'Invitada')
        self.assertEqual(reserva.estado, 'CONFIRMADA')
        self.assertRedirects(respuesta, reverse('hotel:confirmacion', args=[reserva.codigo]))

    def test_doble_envio_no_duplica_reserva_ni_servicios(self):
        respuesta = self.preparar()
        datos = self.datos_pago(respuesta)
        primera = self.client.post(reverse('hotel:pago'), datos)
        segunda = self.client.post(reverse('hotel:pago'), datos)
        self.assertEqual(primera.url, segunda.url)
        self.assertEqual(Reserva.objects.count(), 1)
        self.assertEqual(DetalleReserva.objects.count(), 1)

    def test_pago_rechazado_y_formulario_incompleto_no_confirman(self):
        respuesta = self.preparar()
        rechazo = self.client.post(reverse('hotel:pago'), self.datos_pago(respuesta, resultado_pago='RECHAZADO'))
        self.assertContains(rechazo, 'pago de prueba fue rechazado')
        invalido = self.client.post(reverse('hotel:pago'), self.datos_pago(respuesta, acepta_condiciones=''))
        self.assertEqual(invalido.status_code, 200)
        reserva = Reserva.objects.get()
        self.assertEqual(reserva.estado, 'RETENIDA')
        self.assertFalse(reserva.pago_simulado)

    def test_retencion_vencida_no_se_confirma_y_libera_habitacion(self):
        respuesta = self.preparar()
        reserva = respuesta.context['reserva']
        reserva.retencion_hasta = timezone.now()-timedelta(seconds=1)
        reserva.save(update_fields=['retencion_hasta'])
        respuesta = self.client.post(reverse('hotel:pago'), self.datos_pago(respuesta))
        self.assertEqual(respuesta.status_code, 302)
        reserva.refresh_from_db()
        self.assertEqual(reserva.estado, 'CANCELADA')
        otra = self.preparar(registrado=False, cliente=Client()).context['reserva']
        self.assertNotEqual(otra.pk, reserva.pk)

    def test_segundo_cliente_no_retiene_la_misma_habitacion(self):
        self.preparar()
        otro = Client()
        sesion = otro.session
        sesion['seleccion_estadia'] = self.seleccion
        sesion.save()
        respuesta = otro.get(reverse('hotel:pago'))
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(Reserva.objects.count(), 1)

    def test_codigo_solo_no_da_acceso_a_confirmacion_o_datos(self):
        respuesta = self.preparar()
        self.client.post(reverse('hotel:pago'), self.datos_pago(respuesta))
        reserva = Reserva.objects.get()
        otro = Client()
        for ruta in ('confirmacion', 'comprobante'):
            self.assertEqual(otro.get(reverse(f'hotel:{ruta}', args=[reserva.codigo])).status_code, 404)

    def test_revision_alterada_no_confirma_y_catalogo_no_cambia_tarifa_pactada(self):
        respuesta = self.preparar()
        self.habitacion.precio_base_noche = Decimal('99999')
        self.habitacion.save()
        respuesta_error = self.client.post(reverse('hotel:pago'), self.datos_pago(respuesta, revision='alterada'))
        self.assertContains(respuesta_error, 'no es válida')
        reserva = Reserva.objects.get()
        self.assertEqual(reserva.estado, 'RETENIDA')
        self.assertEqual(reserva.precio_noche_pactado, Decimal('20000'))

    def test_dos_retenciones_del_mismo_cliente_no_duplican_bienvenida(self):
        primera = self.preparar()
        otra_habitacion = Habitacion.objects.create(numero=102, tipo='NORMAL', precio_base_noche=Decimal('20000'))
        otro = Client()
        segunda = self.preparar(cliente=otro, seleccion=dict(self.seleccion, habitacion=otra_habitacion.pk))
        self.client.post(reverse('hotel:pago'), self.datos_pago(primera))
        cambio = otro.post(reverse('hotel:pago'), self.datos_pago(segunda))
        self.assertContains(cambio, 'El importe cambió')
        retenida = Reserva.objects.get(habitacion=otra_habitacion)
        self.assertEqual(retenida.estado, 'RETENIDA')
        self.assertEqual(retenida.descuento_aplicado, Decimal('0'))
        datos = self.datos_pago(cambio)
        datos['revision'] = cambio.context['form']['revision'].value()
        self.assertEqual(otro.post(reverse('hotel:pago'), datos).status_code, 302)
        retenida.refresh_from_db()
        self.assertEqual(retenida.estado, 'CONFIRMADA')

    def test_extra_desactivado_revierte_retencion_completa(self):
        self.servicio.activo = False
        self.servicio.save()
        with self.assertRaises(ErrorReserva):
            preparar_pago(self.seleccion, self.usuario)
        self.assertFalse(Reserva.objects.exists())
        self.assertFalse(DetalleReserva.objects.exists())

    def test_reserva_anterior_de_admin_tambien_consume_bienvenida(self):
        Reserva.objects.create(
            cliente=self.cliente_hotel, habitacion=self.habitacion,
            fecha_entrada=timezone.now()-timedelta(days=10),
            fecha_salida=timezone.now()-timedelta(days=8),
            estado='COMPLETADA',
        )
        respuesta = self.preparar()
        self.assertEqual(respuesta.context['reserva'].descuento_aplicado, Decimal('0'))
        self.assertEqual(respuesta.context['reserva'].monto_total, Decimal('50000'))


class ConcurrenciaRetencionTests(TransactionTestCase):
    def test_dos_solicitudes_concurrentes_crean_como_maximo_una_retencion(self):
        habitacion = Habitacion.objects.create(numero=101, tipo='NORMAL', precio_base_noche=Decimal('20000'))
        seleccion = {'habitacion': habitacion.pk, 'entrada': (timezone.localdate()+timedelta(days=7)).isoformat(), 'salida': (timezone.localdate()+timedelta(days=9)).isoformat(), 'huespedes': 2, 'extras': {}}
        barrera = Barrier(2)
        def intentar():
            connections.close_all()
            barrera.wait(timeout=10)
            try:
                return preparar_pago(seleccion).pk
            except (ErrorReserva, OperationalError):
                return None
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(lambda _: intentar(), range(2)))
        self.assertEqual(sum(r is not None for r in resultados), 1)
        self.assertEqual(Reserva.objects.filter(estado='RETENIDA').count(), 1)
