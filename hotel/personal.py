from functools import wraps
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import BitacoraReserva, Habitacion, PersonalHotel, Reserva
from .reservas import hay_conflicto


def personal_de(usuario):
    if not usuario.is_authenticated or not usuario.is_active:
        return None
    perfil = PersonalHotel.objects.filter(usuario=usuario).first()
    if perfil is None and usuario.is_superuser:
        perfil = PersonalHotel.objects.get_or_create(usuario=usuario, defaults={'codigo_empleado': f'ADM-{usuario.pk}', 'rol': PersonalHotel.Rol.ADMINISTRADOR})[0]
    return perfil


def acceso_personal(solo_admin=False):
    def decorador(vista):
        @wraps(vista)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path(), login_url='hotel:ingresar')
            perfil = personal_de(request.user)
            if not perfil or (solo_admin and not request.user.is_superuser and perfil.rol != PersonalHotel.Rol.ADMINISTRADOR):
                raise PermissionDenied('No tienes permisos para esta sección.')
            request.personal_hotel = perfil
            request.es_administrador = request.user.is_superuser or perfil.rol == PersonalHotel.Rol.ADMINISTRADOR
            return vista(request, *args, **kwargs)
        return wrapper
    return decorador


def instantanea(reserva):
    valores = {campo: getattr(reserva, campo) for campo in (
        'estado', 'habitacion_id', 'numero_huespedes', 'titular_nombre', 'titular_apellido',
        'titular_rut', 'titular_correo', 'titular_telefono',
    )}
    for campo in ('fecha_entrada', 'fecha_salida', 'fecha_checkin', 'fecha_checkout', 'fecha_confirmacion'):
        fecha = getattr(reserva, campo)
        valores[campo] = fecha.isoformat() if fecha else None
    for campo in ('precio_noche_pactado', 'monto_total', 'descuento_aplicado'):
        valores[campo] = str(getattr(reserva, campo))
    valores['extras'] = [{'servicio_id': d.servicio_id, 'cantidad': d.cantidad, 'precio_unitario': str(d.precio_unitario), 'observaciones': d.observaciones} for d in reserva.detalles.order_by('pk')]
    return valores


def auditar(personal, accion, motivo, reserva=None, habitacion=None, cliente=None, antes=None, despues=None):
    return BitacoraReserva.objects.create(
        personal=personal, accion=accion, motivo=motivo,
        reserva=reserva, habitacion=habitacion or (reserva.habitacion if reserva else None),
        cliente=cliente or (reserva.cliente if reserva else None),
        datos_antes=antes or {}, datos_despues=despues or {},
    )


@transaction.atomic
def registrar_checkin(reserva_id, personal):
    reserva = Reserva.objects.select_related('habitacion').get(pk=reserva_id)
    if reserva.estado == Reserva.Estado.EN_ESTADIA:
        return reserva, False
    if reserva.estado != Reserva.Estado.CONFIRMADA:
        raise ValidationError('Solo puedes registrar la entrada de una reserva confirmada.')
    if timezone.localtime(reserva.fecha_entrada).date() != timezone.localdate():
        raise ValidationError('El check-in se realiza el día de entrada indicado en la reserva.')
    if reserva.habitacion.estado_actual in (Habitacion.Estado.MANTENIMIENTO, Habitacion.Estado.OCUPADA) or Reserva.objects.filter(habitacion_id=reserva.habitacion_id, estado=Reserva.Estado.EN_ESTADIA).exclude(pk=reserva.pk).exists():
        raise ValidationError('La habitación no está lista para recibir al huésped: está ocupada o en mantenimiento.')
    if not reserva.titular_rut or not reserva.titular_nombre:
        raise ValidationError('Completa los datos del titular antes de registrar su entrada.')
    antes = instantanea(reserva)
    reserva.estado, reserva.fecha_checkin, reserva.personal = Reserva.Estado.EN_ESTADIA, timezone.now(), personal
    reserva.save(update_fields=['estado', 'fecha_checkin', 'personal'])
    Habitacion.objects.filter(pk=reserva.habitacion_id).update(estado_actual=Habitacion.Estado.OCUPADA)
    auditar(personal, 'CHECK_IN', 'Entrada presencial registrada por recepción.', reserva=reserva, antes=antes, despues=instantanea(reserva))
    return reserva, True


@transaction.atomic
def registrar_checkout(reserva_id, personal):
    reserva = Reserva.objects.select_related('habitacion').get(pk=reserva_id)
    if reserva.estado == Reserva.Estado.COMPLETADA:
        return reserva, False
    if reserva.estado != Reserva.Estado.EN_ESTADIA:
        raise ValidationError('Solo puedes registrar la salida de una reserva en estadía.')
    antes = instantanea(reserva)
    reserva.estado, reserva.fecha_checkout, reserva.personal = Reserva.Estado.COMPLETADA, timezone.now(), personal
    reserva.save(update_fields=['estado', 'fecha_checkout', 'personal'])
    if not Reserva.objects.filter(habitacion_id=reserva.habitacion_id, estado=Reserva.Estado.EN_ESTADIA).exists():
        Habitacion.objects.filter(pk=reserva.habitacion_id).update(estado_actual=Habitacion.Estado.DISPONIBLE)
    auditar(personal, 'CHECK_OUT', 'Salida del huésped registrada por recepción.', reserva=reserva, antes=antes, despues=instantanea(reserva))
    return reserva, True


@transaction.atomic
def cancelar_por_personal(reserva_id, personal, motivo):
    if not motivo.strip():
        raise ValidationError('El motivo de cancelación es obligatorio.')
    reserva = Reserva.objects.select_related('habitacion').get(pk=reserva_id)
    if reserva.estado == Reserva.Estado.CANCELADA:
        return reserva, False
    if reserva.estado not in (Reserva.Estado.CONFIRMADA, Reserva.Estado.RETENIDA):
        raise ValidationError('Esta reserva no admite cancelación. Si el huésped ingresó, registra su salida.')
    antes = instantanea(reserva)
    reserva.estado, reserva.retencion_hasta, reserva.personal = Reserva.Estado.CANCELADA, None, personal
    reserva.save(update_fields=['estado', 'retencion_hasta', 'personal'])
    auditar(personal, 'CANCELAR_RESERVA', motivo.strip(), reserva=reserva, antes=antes, despues=instantanea(reserva))
    return reserva, True


@transaction.atomic
def modificar_por_personal(reserva_id, personal, datos):
    if not datos['motivo'].strip():
        raise ValidationError('El motivo de modificación es obligatorio.')
    reserva = Reserva.objects.select_related('habitacion').get(pk=reserva_id)
    if reserva.estado not in (Reserva.Estado.CONFIRMADA, Reserva.Estado.RETENIDA):
        raise ValidationError('Solo puedes modificar reservas confirmadas o retenidas.')
    antes = instantanea(reserva)
    base_anterior = reserva.precio_noche_pactado * reserva.contar_noches(reserva.fecha_entrada, reserva.fecha_salida)
    proporcion = reserva.descuento_aplicado / base_anterior if base_anterior else Decimal('0')
    for campo in ('habitacion', 'fecha_entrada', 'fecha_salida', 'numero_huespedes'):
        setattr(reserva, campo, datos[campo])
    if reserva.fecha_salida <= reserva.fecha_entrada or timezone.localtime(reserva.fecha_entrada).date() < timezone.localdate():
        raise ValidationError('Revisa las fechas: la entrada no puede estar en un día pasado y la salida debe ser posterior.')
    if reserva.habitacion.estado_actual == Habitacion.Estado.MANTENIMIENTO or hay_conflicto(reserva):
        raise ValidationError('La habitación no está disponible para ese rango de fechas.')
    reserva.personal = personal
    reserva.save()
    reserva.descuento_aplicado = (reserva.precio_noche_pactado * reserva.contar_noches(reserva.fecha_entrada, reserva.fecha_salida) * proporcion).quantize(Decimal('0.01'))
    reserva.save(update_fields=['descuento_aplicado'])
    auditar(personal, 'MODIFICAR_RESERVA', datos['motivo'].strip(), reserva=reserva, antes=antes, despues=instantanea(reserva))
    return reserva


@transaction.atomic
def cambiar_estado_habitacion(habitacion_id, personal, estado, motivo):
    if estado not in (Habitacion.Estado.DISPONIBLE, Habitacion.Estado.MANTENIMIENTO) or not motivo.strip():
        raise ValidationError('Selecciona Disponible o Mantenimiento e indica el motivo.')
    habitacion = Habitacion.objects.get(pk=habitacion_id)
    if Reserva.objects.filter(habitacion=habitacion, estado=Reserva.Estado.EN_ESTADIA).exists():
        raise ValidationError('La habitación tiene un huésped alojado. Registra primero su salida.')
    if estado == Habitacion.Estado.MANTENIMIENTO and Reserva.objects.filter(habitacion=habitacion, fecha_salida__gt=timezone.now()).filter(Q(estado=Reserva.Estado.CONFIRMADA) | Q(estado=Reserva.Estado.RETENIDA, retencion_hasta__gt=timezone.now())).exists():
        raise ValidationError('Reasigna o cancela las reservas vigentes antes de poner la habitación en mantenimiento.')
    anterior = habitacion.estado_actual
    if anterior != estado:
        habitacion.estado_actual = estado
        habitacion.save(update_fields=['estado_actual'])
        auditar(personal, 'ESTADO_HABITACION', motivo.strip(), habitacion=habitacion, antes={'estado': anterior}, despues={'estado': estado})
    return habitacion


def datos_dashboard(fecha):
    inicio = timezone.make_aware(datetime.combine(fecha, time.min))
    fin = inicio + timedelta(days=1)
    reservas = list(Reserva.objects.filter(fecha_entrada__lt=fin, fecha_salida__gt=inicio).filter(Q(estado__in=['CONFIRMADA', 'EN_ESTADIA']) | Q(estado='RETENIDA', retencion_hasta__gt=timezone.now())))
    actuales = set(Reserva.objects.filter(estado='EN_ESTADIA').values_list('habitacion_id', flat=True)) if fecha == timezone.localdate() else set()
    habitaciones, conteo = [], {estado: 0 for estado, _ in Habitacion.Estado.choices}
    for habitacion in Habitacion.objects.all():
        relacionadas = [r for r in reservas if r.habitacion_id == habitacion.pk and timezone.localtime(r.fecha_salida).date() > fecha]
        estado = 'DISPONIBLE'
        if habitacion.estado_actual == 'MANTENIMIENTO':
            estado = 'MANTENIMIENTO'
        elif habitacion.pk in actuales or any(r.estado == 'EN_ESTADIA' for r in relacionadas):
            estado = 'OCUPADA'
        elif relacionadas:
            estado = 'RESERVADA'
        elif fecha == timezone.localdate() and habitacion.estado_actual in ('OCUPADA', 'RESERVADA'):
            estado = habitacion.estado_actual
        conteo[estado] += 1
        habitaciones.append({'id': habitacion.pk, 'numero': habitacion.numero, 'tipo': habitacion.get_tipo_display(), 'estado': estado, 'etiqueta': Habitacion.Estado(estado).label})
    return {'habitaciones': habitaciones, 'conteo': conteo, 'fecha': fecha.isoformat(), 'actualizado': timezone.now().isoformat()}
