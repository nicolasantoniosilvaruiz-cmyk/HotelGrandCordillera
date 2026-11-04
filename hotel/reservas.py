"""Retención y confirmación local de reservas. No procesa cobros reales."""
from datetime import datetime, time
from decimal import Decimal, ROUND_HALF_UP

from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Cliente, CuponDescuento, DetalleReserva, Habitacion, Reserva, ServicioAdicional


class ErrorReserva(Exception):
    pass


class RetencionVencida(ErrorReserva):
    pass


def hay_conflicto(reserva):
    return Reserva.objects.filter(
        habitacion_id=reserva.habitacion_id,
        fecha_entrada__lt=reserva.fecha_salida,
        fecha_salida__gt=reserva.fecha_entrada,
    ).exclude(pk=reserva.pk).filter(
        Q(estado__in=[Reserva.Estado.CONFIRMADA, Reserva.Estado.EN_ESTADIA])
        | Q(estado=Reserva.Estado.RETENIDA, retencion_hasta__gt=timezone.now())
    ).exists()


def actualizar_descuento(reserva):
    # Una reserva confirmada alguna vez consume el beneficio, aunque se cancele.
    elegible = reserva.cliente_id and not Reserva.objects.filter(
        cliente_id=reserva.cliente_id,
    ).filter(
        Q(fecha_confirmacion__isnull=False)
        | Q(estado__in=[Reserva.Estado.CONFIRMADA, Reserva.Estado.EN_ESTADIA, Reserva.Estado.COMPLETADA])
    ).exclude(pk=reserva.pk).exists()
    alojamiento = reserva.precio_noche_pactado * reserva.contar_noches(
        reserva.fecha_entrada, reserva.fecha_salida,
    )
    cupones = reserva.cupones.filter(estado='ACTIVO')
    cupones.exclude(cliente_id=reserva.cliente_id, fecha_vencimiento__gte=timezone.localdate()).update(reserva=None)
    cupon = cupones.filter(cliente_id=reserva.cliente_id, fecha_vencimiento__gte=timezone.localdate()).order_by('pk').first() if reserva.cliente_id else None
    porcentaje = cupon.porcentaje / Decimal('100') if cupon else (Decimal('0.05') if elegible else Decimal('0'))
    reserva.descuento_aplicado = (
        (alojamiento * porcentaje).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    )
    reserva.save(update_fields=['descuento_aplicado'])


@transaction.atomic
def preparar_pago(seleccion, usuario=None, reserva_id=None):
    # El proyecto usa SQLite con transaction_mode=IMMEDIATE: la comprobación y
    # la escritura se serializan para evitar dos retenciones del mismo cupo.
    reserva = Reserva.objects.select_related('habitacion', 'cliente__usuario').filter(pk=reserva_id).first() if reserva_id else None
    if reserva is not None:
        if reserva.estado == Reserva.Estado.CONFIRMADA:
            return reserva
        if reserva.estado != Reserva.Estado.RETENIDA or not reserva.retencion_hasta or reserva.retencion_hasta <= timezone.now():
            raise RetencionVencida('El tiempo de tu retención terminó. Vuelve a elegir una habitación.')
        if reserva.habitacion.estado_actual == Habitacion.Estado.MANTENIMIENTO or hay_conflicto(reserva):
            raise ErrorReserva('La habitación ya no está disponible. Revisa otras opciones.')
        if usuario is not None and not reserva.cliente_id:
            reserva.cliente = Cliente.objects.get_or_create(usuario=usuario)[0]
            reserva.save(update_fields=['cliente'])
        actualizar_descuento(reserva)
        reserva.refresh_from_db()
        return reserva

    try:
        entrada = datetime.combine(datetime.fromisoformat(seleccion['entrada']).date(), time(15))
        salida = datetime.combine(datetime.fromisoformat(seleccion['salida']).date(), time(11))
        habitacion = Habitacion.objects.get(pk=seleccion['habitacion'])
        huespedes = int(seleccion['huespedes'])
    except (KeyError, TypeError, ValueError, Habitacion.DoesNotExist):
        raise ErrorReserva('Revisa tu selección de habitación y fechas.')
    entrada = timezone.make_aware(entrada, timezone.get_default_timezone())
    salida = timezone.make_aware(salida, timezone.get_default_timezone())
    if entrada <= timezone.now() or salida <= entrada or not 1 <= huespedes <= 6:
        raise ErrorReserva('Las fechas o la cantidad de huéspedes no son válidas.')
    if habitacion.estado_actual == Habitacion.Estado.MANTENIMIENTO:
        raise ErrorReserva('La habitación se encuentra en mantenimiento.')
    reserva = Reserva(
        habitacion=habitacion, fecha_entrada=entrada, fecha_salida=salida,
        numero_huespedes=huespedes,
        cliente=Cliente.objects.get_or_create(usuario=usuario)[0] if usuario is not None else None,
    )
    if hay_conflicto(reserva):
        raise ErrorReserva('Otra persona está reservando esa habitación. Elige una opción disponible.')
    reserva.save()
    extras = seleccion.get('extras', {})
    for identificador, cantidad in extras.items():
        try:
            servicio = ServicioAdicional.objects.get(pk=identificador, activo=True)
        except (ServicioAdicional.DoesNotExist, ValueError, TypeError):
            raise ErrorReserva('Uno de los extras ya no está disponible. Revisa tu selección.')
        if not isinstance(cantidad, int) or not 1 <= cantidad <= 99:
            raise ErrorReserva('La cantidad de un extra no es válida.')
        DetalleReserva.objects.create(
            reserva=reserva, servicio=servicio, cantidad=cantidad,
            precio_unitario=servicio.precio,
        )
    actualizar_descuento(reserva)
    reserva.refresh_from_db()
    return reserva


def revision_pago(reserva):
    return signing.dumps({'reserva': reserva.pk, 'total': str(reserva.monto_total)}, salt='hotel.pago-prueba')


@transaction.atomic
def confirmar_pago(reserva_id, datos, revision):
    reserva = Reserva.objects.select_related('habitacion').get(pk=reserva_id)
    # Repetir la misma petición nunca genera una segunda reserva.
    if reserva.estado == Reserva.Estado.CONFIRMADA and reserva.pago_simulado:
        return reserva, None
    if reserva.estado != Reserva.Estado.RETENIDA or not reserva.retencion_hasta or reserva.retencion_hasta <= timezone.now():
        raise RetencionVencida('El tiempo de tu retención terminó. Vuelve a consultar disponibilidad.')
    if reserva.habitacion.estado_actual == Habitacion.Estado.MANTENIMIENTO or hay_conflicto(reserva):
        raise ErrorReserva('La habitación ya no está disponible. No se confirmó la reserva.')
    try:
        revision_validada = signing.loads(revision, salt='hotel.pago-prueba', max_age=300)
    except signing.BadSignature:
        raise ErrorReserva('La revisión del importe venció o no es válida. Revisa la pantalla y vuelve a confirmar.')
    actualizar_descuento(reserva)
    reserva.refresh_from_db()
    if revision_validada != {'reserva': reserva.pk, 'total': str(reserva.monto_total)}:
        return reserva, 'El importe cambió. Revisa el nuevo total antes de volver a confirmar.'
    if datos['resultado_pago'] == 'RECHAZADO':
        return reserva, 'El pago de prueba fue rechazado. Puedes intentarlo otra vez dentro del tiempo disponible.'
    for campo in ('rut', 'nombre', 'apellido', 'correo', 'telefono'):
        setattr(reserva, f'titular_{campo}', datos[f'titular_{campo}'])
    reserva.estado = Reserva.Estado.CONFIRMADA
    reserva.fecha_confirmacion = timezone.now()
    reserva.retencion_hasta = None
    reserva.pago_simulado = True
    reserva.save()
    elegido = reserva.cupones.filter(estado='ACTIVO', cliente_id=reserva.cliente_id, fecha_vencimiento__gte=timezone.localdate()).order_by('pk').first()
    if elegido:
        elegido.estado = 'USADO'
        elegido.save(update_fields=['estado'])
        reserva.cupones.filter(estado='ACTIVO').update(reserva=None)
    return reserva, None


@transaction.atomic
def aplicar_cupon_reserva(reserva_id, usuario, codigo):
    reserva = Reserva.objects.select_related('cliente__usuario').get(pk=reserva_id)
    if not usuario.is_authenticated or not reserva.cliente_id or reserva.cliente.usuario_id != usuario.pk:
        raise ErrorReserva('Inicia sesión con la cuenta titular para utilizar un cupón.')
    if reserva.estado != 'RETENIDA' or not reserva.retencion_hasta or reserva.retencion_hasta <= timezone.now():
        raise RetencionVencida('Tu retención venció. Selecciona nuevamente una habitación.')
    if not codigo.strip():
        reserva.cupones.filter(estado='ACTIVO').update(reserva=None)
    else:
        cupon = CuponDescuento.objects.select_related('reserva').filter(codigo__iexact=codigo.strip(), cliente_id=reserva.cliente_id, estado='ACTIVO', fecha_vencimiento__gte=timezone.localdate()).first()
        if not cupon:
            raise ErrorReserva('El cupón no está disponible para tu cuenta o ya venció.')
        if cupon.reserva_id and cupon.reserva_id != reserva.pk:
            otra = cupon.reserva
            if otra.estado == 'CONFIRMADA' or (otra.estado == 'RETENIDA' and otra.retencion_hasta and otra.retencion_hasta > timezone.now()):
                raise ErrorReserva('El cupón ya está asociado a otra reserva en proceso.')
        reserva.cupones.filter(estado='ACTIVO').exclude(pk=cupon.pk).update(reserva=None)
        cupon.reserva = reserva
        cupon.save(update_fields=['reserva'])
    actualizar_descuento(reserva)
    return reserva


def liberar_retencion(reserva_id):
    Reserva.objects.filter(pk=reserva_id, estado=Reserva.Estado.RETENIDA).update(
        estado=Reserva.Estado.CANCELADA, retencion_hasta=None,
    )
