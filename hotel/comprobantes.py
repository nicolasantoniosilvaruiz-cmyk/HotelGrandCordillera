from datetime import timedelta

from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone

from .models import Reserva, SolicitudExtra


def extras_facturados(reserva):
    return [
        *reserva.detalles.select_related('servicio'),
        *reserva.solicitudes_extra.filter(estado=SolicitudExtra.Estado.CONFIRMADA).select_related('servicio'),
    ]


def texto_comprobante(reserva, alojamiento, detalles):
    lineas = [
        'HOTEL GRAND CORDILLERA', 'Comprobante de reserva', '',
        f'Código: {reserva.codigo}', f'Estado: {reserva.get_estado_display()}',
        f'Titular: {reserva.titular_nombre} {reserva.titular_apellido}',
        f'RUT: {reserva.titular_rut}', f'Correo: {reserva.titular_correo}',
        f'Habitación: {reserva.habitacion.numero} - {reserva.habitacion.get_tipo_display()}',
        f'Huéspedes: {reserva.numero_huespedes}',
        f'Entrada: {timezone.localtime(reserva.fecha_entrada):%d/%m/%Y %H:%M}',
        f'Salida: {timezone.localtime(reserva.fecha_salida):%d/%m/%Y %H:%M}',
        f'Alojamiento: ${alojamiento:,.2f} CLP',
    ]
    lineas += [f'{d.servicio.nombre} x {d.cantidad}: ${d.subtotal:,.2f} CLP' for d in detalles]
    lineas += [f'Descuento: ${reserva.descuento_aplicado:,.2f} CLP', f'Total: ${reserva.monto_total:,.2f} CLP']
    lineas += [
        f'Pago inicial simulado: ${reserva.monto_pagado_inicial or 0:,.2f} CLP',
        f'Pagos adicionales simulados: ${reserva.monto_pagado_adicional:,.2f} CLP',
        f'Saldo pendiente: ${reserva.saldo_pendiente:,.2f} CLP',
    ]
    if reserva.pago_simulado:
        lineas += ['', 'PAGO DE PRUEBA APROBADO. No se realizó ningún cargo real.']
    lineas += ['', 'Este comprobante no constituye una boleta o factura.']
    return '\n'.join(lineas)


def enviar_confirmacion_reserva(reserva, personal=None):
    with transaction.atomic():
        reserva = Reserva.objects.get(pk=reserva.pk)
        if reserva.correo_estado == Reserva.EstadoCorreo.ENVIADO:
            return True
        if reserva.estado != Reserva.Estado.CONFIRMADA:
            return False
        if (reserva.correo_estado == Reserva.EstadoCorreo.ENVIANDO and reserva.correo_intento
                and reserva.correo_intento > timezone.now() - timedelta(minutes=5)):
            return False
        if not reserva.correo_contenido:
            noches = reserva.contar_noches(reserva.fecha_entrada, reserva.fecha_salida)
            reserva.correo_contenido = texto_comprobante(
                reserva, reserva.precio_noche_pactado * noches, extras_facturados(reserva),
            )
        reserva.correo_estado = Reserva.EstadoCorreo.ENVIANDO
        reserva.correo_intento = timezone.now()
        reserva.correo_error = ''
        reserva.save(update_fields=['correo_estado', 'correo_intento', 'correo_error', 'correo_contenido'])

    try:
        correo = EmailMessage(
            subject=f'Reserva confirmada {reserva.codigo} · Hotel Grand Cordillera',
            body=f'Hola, {reserva.titular_nombre}. Tu reserva está confirmada.\n\n{reserva.correo_contenido}',
            to=[reserva.titular_correo],
        )
        correo.attach(f'reserva-{reserva.codigo}.txt', reserva.correo_contenido, 'text/plain')
        if correo.send() != 1:
            raise RuntimeError('El servidor de correo no aceptó el mensaje.')
    except Exception as error:
        estado, detalle = Reserva.EstadoCorreo.ERROR, str(error)[:500]
    else:
        estado, detalle = Reserva.EstadoCorreo.ENVIADO, ''

    with transaction.atomic():
        Reserva.objects.filter(pk=reserva.pk, correo_intento=reserva.correo_intento).update(
            correo_estado=estado, correo_error=detalle,
            correo_enviado=timezone.now() if estado == Reserva.EstadoCorreo.ENVIADO else None,
        )
        if personal is not None:
            from .personal import auditar
            auditar(personal, 'CORREO_CONFIRMACION', 'Envío de confirmación solicitado desde recepción.',
                    reserva=reserva, despues={'estado': estado})
    return estado == Reserva.EstadoCorreo.ENVIADO
