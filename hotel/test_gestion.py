from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Cliente, DetalleReserva, Habitacion, Reserva, ServicioAdicional, Usuario
from .views import habitaciones_disponibles


class GestionReservasTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(email='ana@example.com', rut='11111111-1', nombre='Ana', apellido='Perez', telefono='912345678')
        self.cliente_hotel = Cliente.objects.create(usuario=self.usuario)
        self.otro_usuario = Usuario.objects.create_user(email='luis@example.com', rut='22222222-2', nombre='Luis', apellido='Soto', telefono='912345679')
        self.otro_cliente = Cliente.objects.create(usuario=self.otro_usuario)
        self.habitacion = Habitacion.objects.create(numero=101, tipo='NORMAL', precio_base_noche=Decimal('20000'))
        self.entrada = timezone.now()+timedelta(days=10)
        self.reserva = Reserva.objects.create(
            codigo='ABCD1234', cliente=self.cliente_hotel, habitacion=self.habitacion,
            fecha_entrada=self.entrada, fecha_salida=self.entrada+timedelta(days=2),
            estado='CONFIRMADA', fecha_confirmacion=timezone.now(),
            descuento_aplicado=Decimal('2000'), numero_huespedes=2, pago_simulado=True,
        )

    def consultar(self, cliente=None, codigo=None, rut='11.111.111-1'):
        return (cliente or self.client).post(reverse('hotel:mis_reservas'), {'codigo': codigo or self.reserva.codigo, 'rut': rut})

    def cancelar(self, cliente=None, codigo=None, **datos):
        return (cliente or self.client).post(reverse('hotel:cancelar_reserva', args=[codigo or self.reserva.codigo]), dict({'confirmar': 'on'}, **datos))

    def test_publico_sin_consulta_muestra_formulario_sin_datos_privados(self):
        respuesta = self.client.get(reverse('hotel:mis_reservas'))
        self.assertContains(respuesta, 'Consultar reserva')
        self.assertNotContains(respuesta, self.reserva.codigo)
        self.assertNotContains(respuesta, self.usuario.email)
        self.assertNotContains(respuesta, 'Cancelar reserva')
        self.assertIn('no-store', respuesta.headers['Cache-Control'])

    def test_consulta_con_codigo_y_rut_normalizados_y_variante_sin_descuento(self):
        respuesta = self.consultar(codigo='abcd1234')
        self.assertEqual(respuesta.context['reserva'], self.reserva)
        self.assertContains(respuesta, 'Cancelar reserva')
        self.assertContains(respuesta, 'Descuento registrado en la reserva')
        self.reserva.descuento_aplicado=0
        self.reserva.save(update_fields=['descuento_aplicado'])
        respuesta = self.consultar()
        self.assertNotContains(respuesta, 'Descuento registrado en la reserva')
        self.assertEqual(respuesta.context['reserva'].monto_total, Decimal('40000'))

    def test_datos_incorrectos_no_revelan_si_el_codigo_existe(self):
        primera = self.consultar(rut='99999999-9')
        segunda = self.consultar(codigo='NOEXISTE')
        mensaje = 'No encontramos una reserva con ese código y RUT.'
        self.assertContains(primera, mensaje)
        self.assertContains(segunda, mensaje)
        self.assertNotContains(primera, self.usuario.email)
        self.assertNotContains(primera, 'Cancelar reserva')

    def test_sesion_muestra_todas_las_propias_sin_pedir_codigo_ni_rut(self):
        Reserva.objects.create(codigo='OTRA1234', cliente=self.otro_cliente, habitacion=self.habitacion, fecha_entrada=self.entrada+timedelta(days=5), fecha_salida=self.entrada+timedelta(days=7))
        segunda = Reserva.objects.create(codigo='MIA12345', cliente=self.cliente_hotel, habitacion=self.habitacion, fecha_entrada=self.entrada+timedelta(days=8), fecha_salida=self.entrada+timedelta(days=10))
        self.client.force_login(self.usuario)
        respuesta = self.client.get(reverse('hotel:mis_reservas'))
        self.assertContains(respuesta, self.reserva.codigo)
        self.assertContains(respuesta, segunda.codigo)
        self.assertNotContains(respuesta, 'OTRA1234')
        self.assertNotContains(respuesta, 'name="codigo"')
        self.assertNotContains(respuesta, 'name="rut"')
        self.assertEqual(len(respuesta.context['reservas']), 2)

    def test_sesion_sin_reservas_tiene_estado_vacio(self):
        self.client.force_login(self.otro_usuario)
        respuesta = self.client.get(reverse('hotel:mis_reservas'))
        self.assertContains(respuesta, 'Todavía no tienes reservas')
        self.assertNotContains(respuesta, self.reserva.codigo)
        self.assertNotContains(respuesta, 'Cancelar reserva')

    def test_cancelacion_publica_persiste_libera_fechas_y_conserva_detalles(self):
        extra = ServicioAdicional.objects.create(nombre='Desayuno', precio=Decimal('5000'))
        DetalleReserva.objects.create(reserva=self.reserva, servicio=extra, cantidad=2, precio_unitario=extra.precio)
        self.reserva.refresh_from_db()
        importe = self.reserva.monto_total
        self.assertFalse(habitaciones_disponibles(self.entrada.date(), (self.entrada+timedelta(days=2)).date()).exists())
        self.consultar()
        respuesta = self.cancelar()
        self.assertRedirects(respuesta, reverse('hotel:mis_reservas'))
        self.reserva.refresh_from_db()
        self.assertEqual(self.reserva.estado, 'CANCELADA')
        self.assertEqual(self.reserva.monto_total, importe)
        self.assertEqual(self.reserva.detalles.count(), 1)
        self.assertTrue(habitaciones_disponibles(self.entrada.date(), (self.entrada+timedelta(days=2)).date()).filter(pk=self.habitacion.pk).exists())
        self.assertContains(self.client.get(reverse('hotel:mis_reservas')), 'Tu reserva está cancelada')

    def test_cancelacion_con_sesion_y_doble_envio_no_borra_reserva(self):
        self.client.force_login(self.usuario)
        self.assertEqual(self.cancelar().status_code, 302)
        self.assertEqual(self.cancelar().status_code, 302)
        self.assertEqual(Reserva.objects.count(), 1)
        self.reserva.refresh_from_db()
        self.assertEqual(self.reserva.estado, 'CANCELADA')

    def test_no_permite_cancelar_reservas_ajenas_o_sin_consulta_publica(self):
        self.assertEqual(self.cancelar().status_code, 404)
        self.client.force_login(self.otro_usuario)
        self.assertEqual(self.cancelar().status_code, 404)
        self.assertEqual(self.client.get(reverse('hotel:mis_reservas'), {'reserva': self.reserva.codigo}).status_code, 404)
        self.reserva.refresh_from_db()
        self.assertEqual(self.reserva.estado, 'CONFIRMADA')

    def test_requiere_casilla_y_post(self):
        self.consultar()
        respuesta = self.cancelar(confirmar='')
        self.assertEqual(respuesta.status_code, 302)
        self.reserva.refresh_from_db()
        self.assertEqual(self.reserva.estado, 'CONFIRMADA')
        self.assertEqual(self.client.get(reverse('hotel:cancelar_reserva', args=[self.reserva.codigo])).status_code, 405)

    def test_limite_incluye_exactamente_tres_horas_y_rechaza_un_segundo_despues(self):
        self.client.force_login(self.usuario)
        limite = self.reserva.fecha_entrada-timedelta(hours=3)
        with patch('hotel.gestion_views.timezone.now', return_value=limite+timedelta(seconds=1)):
            respuesta = self.client.get(reverse('hotel:mis_reservas'))
            self.assertNotContains(respuesta, 'data-cancel-form')
            self.cancelar()
        self.reserva.refresh_from_db()
        self.assertEqual(self.reserva.estado, 'CONFIRMADA')
        with patch('hotel.gestion_views.timezone.now', return_value=limite):
            self.cancelar()
        self.reserva.refresh_from_db()
        self.assertEqual(self.reserva.estado, 'CANCELADA')

    def test_revalida_estado_al_cancelar_y_no_cancela_checkin(self):
        self.client.force_login(self.usuario)
        self.client.get(reverse('hotel:mis_reservas'))
        self.reserva.estado = 'EN_ESTADIA'
        self.reserva.save(update_fields=['estado'])
        self.cancelar()
        self.reserva.refresh_from_db()
        self.assertEqual(self.reserva.estado, 'EN_ESTADIA')

    def test_autorizacion_publica_caduca_y_consulta_fallida_la_revoca(self):
        self.consultar()
        sesion = self.client.session
        permiso = sesion['gestion_consulta']
        permiso['vence'] = timezone.now().timestamp()-1
        sesion['gestion_consulta'] = permiso
        sesion.save()
        self.assertEqual(self.cancelar().status_code, 404)
        self.consultar()
        self.consultar(rut='99999999-9')
        self.assertEqual(self.cancelar().status_code, 404)

    def test_csrf_es_obligatorio_y_no_coloca_rut_en_url(self):
        protegido = Client(enforce_csrf_checks=True)
        self.assertEqual(self.consultar(cliente=protegido).status_code, 403)
        respuesta = self.consultar()
        self.assertNotContains(respuesta, '?rut=')

    def test_enlace_desde_confirmacion_y_confirmacion_cancelada_no_miente(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.get(reverse('hotel:confirmacion', args=[self.reserva.codigo]))
        self.assertContains(respuesta, '/mi-reserva/?reserva=')
        self.cancelar()
        respuesta = self.client.get(reverse('hotel:confirmacion', args=[self.reserva.codigo]))
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn('/mi-reserva/', respuesta.url)
