from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Usuario, Cliente, Habitacion, Reserva


class ReservaAutocompletadoTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            email='cliente@example.com', rut='11111111-1', nombre='Ana',
            apellido='Perez', telefono='912345678',
        )
        self.cliente_hotel = Cliente.objects.create(usuario=self.usuario)
        self.habitacion = Habitacion.objects.create(
            numero=101, tipo=Habitacion.Tipo.NORMAL,
            precio_base_noche=Decimal('45000'),
        )

    def reserva(self, **extra):
        datos = dict(
            cliente=self.cliente_hotel, habitacion=self.habitacion,
            fecha_entrada=timezone.now() + timedelta(days=1),
            fecha_salida=timezone.now() + timedelta(days=3),
        )
        datos.update(extra)
        return Reserva.objects.create(**datos)

    def test_copia_cliente_y_tarifa_sin_entrada_manual(self):
        reserva = self.reserva(titular_nombre='Dato manual ignorado')
        reserva.refresh_from_db()
        for campo, origen in (
            ('rut', 'rut'), ('nombre', 'nombre'), ('apellido', 'apellido'),
            ('correo', 'email'), ('telefono', 'telefono'),
        ):
            self.assertEqual(getattr(reserva, f'titular_{campo}'), getattr(self.usuario, origen))
        self.assertEqual(reserva.precio_noche_pactado, Decimal('45000'))

    def test_conserva_historial_al_cambiar_catalogo_y_perfil(self):
        reserva = self.reserva()
        self.usuario.nombre = 'Nombre nuevo'
        self.usuario.save()
        self.habitacion.precio_base_noche = Decimal('60000')
        self.habitacion.save()
        reserva.observacion_temporal = 'Editar sin cambiar relaciones'
        reserva.save()
        reserva.refresh_from_db()
        self.assertEqual(reserva.titular_nombre, 'Ana')
        self.assertEqual(reserva.precio_noche_pactado, Decimal('45000'))

    def test_cambiar_relaciones_actualiza_solo_los_datos_correspondientes(self):
        reserva = self.reserva()
        otro_usuario = Usuario.objects.create_user(
            email='otro@example.com', rut='22222222-2', nombre='Luis',
            apellido='Soto', telefono='998765432',
        )
        otro_cliente = Cliente.objects.create(usuario=otro_usuario)
        otra_habitacion = Habitacion.objects.create(
            numero=102, tipo=Habitacion.Tipo.MEDIANA,
            precio_base_noche=Decimal('70000'),
        )
        reserva.cliente = otro_cliente
        reserva.save(update_fields=['cliente'])
        reserva.refresh_from_db()
        self.assertEqual(reserva.titular_nombre, 'Luis')
        self.assertEqual(reserva.precio_noche_pactado, Decimal('45000'))
        reserva.habitacion = otra_habitacion
        reserva.save(update_fields=['habitacion'])
        reserva.refresh_from_db()
        self.assertEqual(reserva.precio_noche_pactado, Decimal('70000'))

    def test_invitado_conserva_datos_manuales(self):
        reserva = self.reserva(
            cliente=None, titular_nombre='Invitada',
            titular_correo='invitada@example.com',
        )
        reserva.refresh_from_db()
        self.assertEqual(reserva.titular_nombre, 'Invitada')
        self.assertEqual(reserva.titular_correo, 'invitada@example.com')
        self.assertEqual(reserva.precio_noche_pactado, Decimal('45000'))

    def test_guardado_parcial_no_copia_relaciones_no_guardadas(self):
        reserva = self.reserva()
        otra = Habitacion.objects.create(
            numero=103, tipo=Habitacion.Tipo.NORMAL,
            precio_base_noche=Decimal('90000'),
        )
        reserva.habitacion = otra
        reserva.estado = Reserva.Estado.CONFIRMADA
        reserva.save(update_fields=['estado'])
        reserva.refresh_from_db()
        self.assertEqual(reserva.habitacion_id, self.habitacion.pk)
        self.assertEqual(reserva.precio_noche_pactado, Decimal('45000'))

    def test_administrador_crea_sin_titular_ni_tarifa_y_muestra_solo_lectura(self):
        admin = Usuario.objects.create_superuser(
            email='admin@example.com', password='Prueba-solo-local-923',
            rut='33333333-3', nombre='Admin', apellido='Hotel', telefono='912345679',
        )
        self.client.force_login(admin)
        url = reverse('admin:hotel_reserva_add')
        respuesta = self.client.get(url)
        self.assertEqual(respuesta.status_code, 200)
        datos = {
            'numero_huespedes': '2',
            'cliente': self.cliente_hotel.pk,
            'habitacion': self.habitacion.pk,
            'fecha_entrada_0': '2027-01-10', 'fecha_entrada_1': '14:00:00',
            'fecha_salida_0': '2027-01-12', 'fecha_salida_1': '12:00:00',
            'estado': Reserva.Estado.RETENIDA,
            'descuento_aplicado': '0', 'monto_total': '0',
            'detalles-TOTAL_FORMS': '0', 'detalles-INITIAL_FORMS': '0',
            'detalles-MIN_NUM_FORMS': '0', 'detalles-MAX_NUM_FORMS': '1000',
            '_save': 'Guardar',
        }
        respuesta = self.client.post(url, datos)
        errores = respuesta.context['adminform'].form.errors if respuesta.status_code == 200 else None
        self.assertEqual(respuesta.status_code, 302, errores)
        reserva = Reserva.objects.get()
        self.assertEqual(reserva.titular_nombre, 'Ana')
        self.assertEqual(reserva.precio_noche_pactado, Decimal('45000'))
        respuesta = self.client.get(reverse('admin:hotel_reserva_change', args=[reserva.pk]))
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotIn('titular_nombre', respuesta.context['adminform'].form.fields)
        self.assertNotIn('precio_noche_pactado', respuesta.context['adminform'].form.fields)
