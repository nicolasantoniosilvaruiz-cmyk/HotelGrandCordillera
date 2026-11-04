import secrets
from django.conf import settings
from django.core.mail import EmailMessage
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import CampanaPromocion, Cliente, CuponDescuento, EnvioPromocion
from .personal import auditar


@transaction.atomic
def crear_campana(personal, datos):
    existente = CampanaPromocion.objects.filter(token=datos['token']).first()
    if existente:
        if existente.personal_id != personal.pk:
            raise PermissionDenied
        return existente, False
    if datos['fecha_vencimiento'] < timezone.localdate():
        raise ValidationError('El vencimiento no puede estar en una fecha pasada.')
    clientes = list(Cliente.objects.filter(pk__in=[c.pk for c in datos['clientes']], usuario__is_active=True).select_related('usuario'))
    if not clientes:
        raise ValidationError('Selecciona al menos un cliente activo.')
    campana = CampanaPromocion.objects.create(
        personal=personal, token=datos['token'], asunto=datos['asunto'], mensaje=datos['mensaje'],
        porcentaje=datos['porcentaje'], fecha_vencimiento=datos['fecha_vencimiento'],
    )
    for cliente in clientes:
        cupon = CuponDescuento.objects.create(
            codigo='PROMO' + secrets.token_hex(6).upper(), cliente=cliente,
            porcentaje=campana.porcentaje, fecha_vencimiento=campana.fecha_vencimiento,
        )
        cuerpo = (
            f'Hola, {cliente.usuario.nombre}:\n\n{campana.mensaje}\n\n'
            f'Tu código personal: {cupon.codigo}\n'
            f'Descuento: {campana.porcentaje}% solo en alojamiento. Los extras no se descuentan.\n'
            f'Válido hasta el {campana.fecha_vencimiento:%d/%m/%Y}, una vez, al iniciar sesión con tu cuenta.\n'
            'No acumulable con el descuento de bienvenida ni con otros cupones.\n\nHotel Grand Cordillera'
        )
        EnvioPromocion.objects.create(campana=campana, cliente=cliente, cupon=cupon, destinatario=cliente.usuario.email, cuerpo=cuerpo)
    auditar(personal, 'CREAR_PROMOCION', campana.asunto, despues={'campana': campana.pk, 'porcentaje': str(campana.porcentaje), 'clientes': [c.pk for c in clientes], 'vencimiento': str(campana.fecha_vencimiento)})
    return campana, True


def enviar_campana(campana, personal, reintentar=False):
    estados = ['ERROR'] if reintentar else ['PENDIENTE']
    for envio_id in campana.envios.filter(estado__in=estados).values_list('pk', flat=True):
        # Reclamar cada envío evita duplicarlo por doble clic o peticiones simultáneas.
        with transaction.atomic():
            if not EnvioPromocion.objects.filter(pk=envio_id, estado__in=estados).update(estado='ENVIANDO'):
                continue
        envio = EnvioPromocion.objects.select_related('cliente').get(pk=envio_id)
        try:
            resultado = EmailMessage(campana.asunto, envio.cuerpo, settings.DEFAULT_FROM_EMAIL, [envio.destinatario]).send(fail_silently=False)
            if resultado != 1:
                raise RuntimeError('El servicio de correo no aceptó el mensaje.')
        except Exception as error:
            estado, detalle = 'ERROR', str(error)[:500]
        else:
            estado, detalle = 'ENVIADO', ''
        with transaction.atomic():
            EnvioPromocion.objects.filter(pk=envio.pk).update(estado=estado, error=detalle, fecha_envio=timezone.now() if estado == 'ENVIADO' else None, backend=settings.EMAIL_BACKEND)
            auditar(personal, 'ENVIAR_PROMOCION' if estado == 'ENVIADO' else 'ERROR_PROMOCION', 'Envío de promoción por el servicio de correo configurado.', cliente=envio.cliente, antes={'envio': envio.pk, 'estado': 'ENVIANDO'}, despues={'envio': envio.pk, 'estado': estado, 'campana': campana.pk})
