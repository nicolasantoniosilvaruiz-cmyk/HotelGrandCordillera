from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Reserva, ServicioAdicional, SolicitudExtra


def catalogo_estadia(reserva):
    return ServicioAdicional.disponibles_para(reserva.habitacion.tipo).exclude(
        categoria=ServicioAdicional.Categoria.RESERVA,
    ).order_by('categoria', 'nombre')


class SolicitarExtraForm(forms.Form):
    servicio = forms.IntegerField(min_value=1)
    cantidad = forms.IntegerField(min_value=1, max_value=99)
    token = forms.UUIDField()


@transaction.atomic
def solicitar_extra(reserva_id, datos):
    reserva = Reserva.objects.select_related('habitacion').get(pk=reserva_id)
    anterior = reserva.solicitudes_extra.filter(token=datos['token']).first()
    if anterior is not None:
        return anterior
    if reserva.estado != Reserva.Estado.EN_ESTADIA:
        raise ValidationError('Solo puedes solicitar extras durante una estadía activa.')
    servicio = catalogo_estadia(reserva).filter(pk=datos['servicio']).first()
    if servicio is None:
        raise ValidationError('Este extra no está disponible para tu habitación.')
    automatica = servicio.categoria == ServicioAdicional.Categoria.OUTDOOR
    solicitud = reserva.solicitudes_extra.create(
        servicio=servicio, categoria=servicio.categoria, cantidad=datos['cantidad'],
        precio_unitario=servicio.precio, token=datos['token'],
        estado=SolicitudExtra.Estado.CONFIRMADA if automatica else SolicitudExtra.Estado.PENDIENTE,
        resuelta_en=timezone.now() if automatica else None,
    )
    if automatica:
        reserva.recalcular_total()
    return solicitud
