from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from .models import Usuario, Habitacion, Reserva, ServicioAdicional, DetalleReserva


class TotalReservaTests(TestCase):
    def setUp(self):
        self.habitacion = Habitacion.objects.create(
            numero=101, tipo='NORMAL', precio_base_noche=Decimal('45000'),
        )
        self.entrada = datetime(2027, 1, 10, 14, tzinfo=ZoneInfo('America/Santiago'))
        self.salida = datetime(2027, 1, 12, 12, tzinfo=ZoneInfo('America/Santiago'))
        self.servicio = ServicioAdicional.objects.create(nombre='Desayuno', precio=Decimal('5000'))

    def reserva(self, **datos):
        return Reserva.objects.create(**dict({
            'habitacion': self.habitacion,
            'fecha_entrada': self.entrada,
            'fecha_salida': self.salida,
        }, **datos))

    def test_dos_noches_y_cambio_de_fechas(self):
        reserva = self.reserva()
        self.assertEqual(reserva.monto_total, Decimal('90000'))
        reserva.fecha_salida += timedelta(days=1)
        reserva.save(update_fields=['fecha_salida'])
        reserva.refresh_from_db()
        self.assertEqual(reserva.monto_total, Decimal('135000'))

    def test_una_noche_no_se_cobra_por_duracion_de_24_horas(self):
        reserva = self.reserva(fecha_salida=self.entrada + timedelta(hours=22))
        self.assertEqual(reserva.monto_total, Decimal('45000'))

    def test_mismo_dia_y_cambio_horario(self):
        self.assertEqual(self.reserva(fecha_salida=self.entrada + timedelta(hours=2)).monto_total, Decimal('45000'))
        entrada = datetime(2027, 4, 2, 14, tzinfo=ZoneInfo('America/Santiago'))
        salida = datetime(2027, 4, 4, 12, tzinfo=ZoneInfo('America/Santiago'))
        self.assertEqual(self.reserva(fecha_entrada=entrada, fecha_salida=salida).monto_total, Decimal('90000'))

    def test_servicios_agregar_editar_eliminar_y_borrado_multiple(self):
        reserva = self.reserva()
        detalle = DetalleReserva.objects.create(
            reserva=reserva, servicio=self.servicio, cantidad=2, precio_unitario=Decimal('5000'),
        )
        reserva.refresh_from_db()
        self.assertEqual(reserva.monto_total, Decimal('100000'))
        detalle.cantidad = 3
        detalle.save(update_fields=['cantidad'])
        reserva.refresh_from_db()
        self.assertEqual(reserva.monto_total, Decimal('105000'))
        detalle.delete()
        reserva.refresh_from_db()
        self.assertEqual(reserva.monto_total, Decimal('90000'))
        DetalleReserva.objects.create(reserva=reserva, servicio=self.servicio, cantidad=1, precio_unitario=Decimal('5000'))
        DetalleReserva.objects.filter(reserva=reserva).delete()
        reserva.refresh_from_db()
        self.assertEqual(reserva.monto_total, Decimal('90000'))

    def test_cambiar_detalle_de_reserva_recalcula_ambas(self):
        primera, segunda = self.reserva(), self.reserva()
        detalle = DetalleReserva.objects.create(reserva=primera, servicio=self.servicio, cantidad=1, precio_unitario=Decimal('5000'))
        detalle.reserva = segunda
        detalle.save(update_fields=['reserva'])
        primera.refresh_from_db()
        segunda.refresh_from_db()
        self.assertEqual(primera.monto_total, Decimal('90000'))
        self.assertEqual(segunda.monto_total, Decimal('95000'))

    def test_descuento_es_importe_y_total_no_negativo(self):
        reserva = self.reserva(descuento_aplicado=Decimal('4500'))
        self.assertEqual(reserva.monto_total, Decimal('85500'))
        reserva.descuento_aplicado = Decimal('999999')
        reserva.save(update_fields=['descuento_aplicado'])
        reserva.refresh_from_db()
        self.assertEqual(reserva.monto_total, Decimal('0'))

    def test_no_usa_cambios_no_guardados_ni_tarifa_nueva(self):
        reserva = self.reserva()
        self.habitacion.precio_base_noche = Decimal('99999')
        self.habitacion.save()
        reserva.fecha_salida += timedelta(days=10)
        reserva.estado = 'CONFIRMADA'
        reserva.save(update_fields=['estado'])
        reserva.refresh_from_db()
        self.assertEqual(reserva.monto_total, Decimal('90000'))
        self.assertEqual(reserva.precio_noche_pactado, Decimal('45000'))

    def test_rechaza_fechas_invalidas_y_permite_eliminar_reserva_con_servicios(self):
        with self.assertRaises(ValidationError):
            self.reserva(fecha_salida=self.entrada)
        reserva = self.reserva()
        DetalleReserva.objects.create(reserva=reserva, servicio=self.servicio, cantidad=1, precio_unitario=Decimal('5000'))
        reserva.delete()
        self.assertFalse(Reserva.objects.exists())

    def test_administrador_calcula_servicios_inline_y_no_permite_total_manual(self):
        admin = Usuario.objects.create_superuser(
            email='totales@example.com', password='Prueba-local-928', rut='55555555-5',
            nombre='Admin', apellido='Hotel', telefono='912345678',
        )
        self.client.force_login(admin)
        datos = {
            'numero_huespedes': '2',
            'habitacion': self.habitacion.pk,
            'fecha_entrada_0': '2027-01-10', 'fecha_entrada_1': '14:00:00',
            'fecha_salida_0': '2027-01-12', 'fecha_salida_1': '12:00:00',
            'estado': 'RETENIDA', 'descuento_aplicado': '4500', 'monto_total': '1',
            'titular_nombre': 'Invitada',
            'detalles-TOTAL_FORMS': '1', 'detalles-INITIAL_FORMS': '0',
            'detalles-MIN_NUM_FORMS': '0', 'detalles-MAX_NUM_FORMS': '1000',
            'detalles-0-servicio': self.servicio.pk, 'detalles-0-cantidad': '2',
            'detalles-0-precio_unitario': '5000', '_save': 'Guardar',
        }
        respuesta = self.client.post(reverse('admin:hotel_reserva_add'), datos)
        errores = respuesta.context['adminform'].form.errors if respuesta.status_code == 200 else None
        self.assertEqual(respuesta.status_code, 302, errores)
        reserva = Reserva.objects.get()
        self.assertEqual(reserva.monto_total, Decimal('95500'))
        url = reverse('admin:hotel_reserva_change', args=[reserva.pk])
        respuesta = self.client.get(url)
        self.assertNotIn('monto_total', respuesta.context['adminform'].form.fields)
        datos.update({
            'detalles-INITIAL_FORMS': '1', 'detalles-0-id': reserva.detalles.get().pk,
            'detalles-0-reserva': reserva.pk, 'detalles-0-DELETE': 'on',
        })
        respuesta = self.client.post(url, datos)
        self.assertEqual(respuesta.status_code, 302)
        reserva.refresh_from_db()
        self.assertEqual(reserva.monto_total, Decimal('85500'))
