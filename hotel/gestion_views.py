"""Consulta del titular y cancelación de sus reservas desde la web."""
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.db import OperationalError, transaction
from django.db.models import Case, IntegerField, Value, When
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .checkout_views import contexto_estadia
from .models import Reserva


def normalizar_rut(valor):
    return (valor or '').strip().replace('.', '').replace(' ', '').upper()


def rut_reserva(reserva):
    if reserva.titular_rut:
        return normalizar_rut(reserva.titular_rut)
    if reserva.cliente_id:
        return normalizar_rut(reserva.cliente.usuario.rut)
    return ''


def firma_titular(reserva):
    return salted_hmac('hotel.gestion.titular', f'{reserva.pk}:{rut_reserva(reserva)}').hexdigest()


class ConsultaReservaForm(forms.Form):
    codigo = forms.CharField(label='Código de reserva', max_length=32, widget=forms.TextInput(attrs={
        'placeholder': 'Ingresa el código de tu reserva', 'autocomplete': 'off',
    }))
    rut = forms.CharField(label='RUT del titular', max_length=20, widget=forms.TextInput(attrs={
        'placeholder': 'Ej.: 12.345.678-5', 'autocomplete': 'off',
    }))

    def clean_codigo(self):
        return self.cleaned_data['codigo'].strip().upper()

    def clean_rut(self):
        return normalizar_rut(self.cleaned_data['rut'])


class CancelarReservaForm(forms.Form):
    confirmar = forms.BooleanField(label='Entiendo que perderé mi reserva y quiero cancelarla.')


def consulta_publica_vigente(request, reserva):
    consulta = request.session.get('gestion_consulta', {})
    return (
        consulta.get('id') == reserva.pk
        and consulta.get('vence', 0) > timezone.now().timestamp()
        and constant_time_compare(consulta.get('firma', ''), firma_titular(reserva))
    )


def tiene_acceso(request, reserva):
    if request.user.is_authenticated:
        return bool(reserva.cliente_id and reserva.cliente.usuario_id == request.user.pk)
    return consulta_publica_vigente(request, reserva)


def puede_cancelar(reserva, ahora=None):
    ahora = ahora or timezone.now()
    return (
        reserva.estado == Reserva.Estado.CONFIRMADA
        and ahora <= reserva.fecha_entrada - timedelta(hours=3)
    )


def texto_estado(reserva):
    if reserva.estado == Reserva.Estado.RETENIDA and reserva.retencion_hasta and reserva.retencion_hasta <= timezone.now():
        return 'Retención vencida'
    return reserva.get_estado_display()


@never_cache
def mis_reservas(request):
    reserva = None
    reservas = []
    consulta_realizada = False
    form = None
    if request.user.is_authenticated:
        reservas = list(Reserva.objects.filter(cliente__usuario=request.user).select_related(
            'habitacion', 'cliente__usuario',
        ).annotate(prioridad=Case(
            When(estado=Reserva.Estado.CONFIRMADA, then=Value(0)),
            When(estado=Reserva.Estado.EN_ESTADIA, then=Value(1)),
            default=Value(2), output_field=IntegerField(),
        )).order_by('prioridad', '-fecha_creacion'))
        for opcion in reservas:
            opcion.estado_gestion = texto_estado(opcion)
        codigo = request.GET.get('reserva')
        reserva = next((r for r in reservas if r.codigo == codigo), None) if codigo else (reservas[0] if reservas else None)
        if codigo and reserva is None:
            raise Http404
    else:
        form = ConsultaReservaForm(request.POST if request.method == 'POST' else None, initial={
            'codigo': request.GET.get('codigo', ''),
        })
        if request.method == 'POST':
            consulta_realizada = True
            request.session.pop('gestion_consulta', None)
            if form.is_valid():
                candidata = Reserva.objects.select_related('habitacion', 'cliente__usuario').filter(
                    codigo__iexact=form.cleaned_data['codigo'],
                ).first()
                if candidata and rut_reserva(candidata) and constant_time_compare(
                    rut_reserva(candidata), form.cleaned_data['rut'],
                ):
                    reserva = candidata
                    request.session['gestion_consulta'] = {
                        'id': reserva.pk, 'vence': (timezone.now()+timedelta(minutes=15)).timestamp(),
                        'firma': firma_titular(reserva),
                    }
                else:
                    form.add_error(None, 'No encontramos una reserva con ese código y RUT. Revisa los datos e inténtalo de nuevo.')
        else:
            identificador = request.session.get('gestion_consulta', {}).get('id')
            if identificador:
                candidata = Reserva.objects.select_related('habitacion', 'cliente__usuario').filter(pk=identificador).first()
                if candidata and consulta_publica_vigente(request, candidata):
                    reserva = candidata
                    form.initial = {'codigo': reserva.codigo, 'rut': rut_reserva(reserva)}
                else:
                    request.session.pop('gestion_consulta', None)

    contexto = {
        'consulta_form': form, 'reservas': reservas, 'consulta_realizada': consulta_realizada,
        'gestion_activa': True, 'consulta_hora': timezone.now(),
    }
    if reserva is not None:
        contexto.update(contexto_estadia(reserva))
        contexto.update({
            'estado_gestion': texto_estado(reserva),
            'puede_cancelar': puede_cancelar(reserva),
            'cancelar_form': CancelarReservaForm(),
        })
    return render(request, 'hotel/mis_reservas.html', contexto)


@require_POST
@never_cache
def cancelar_reserva(request, codigo):
    try:
        with transaction.atomic():
            reserva = get_object_or_404(Reserva.objects.select_related('cliente__usuario'), codigo=codigo)
            # Comprobar propiedad y plazo dentro de la misma operación que cancela.
            if not tiene_acceso(request, reserva):
                raise Http404
            form = CancelarReservaForm(request.POST)
            if not form.is_valid():
                messages.error(request, 'Marca la casilla de confirmación antes de cancelar tu reserva.')
            elif reserva.estado == Reserva.Estado.CANCELADA:
                messages.info(request, 'Esta reserva ya estaba cancelada.')
            elif puede_cancelar(reserva):
                reserva.estado = Reserva.Estado.CANCELADA
                reserva.retencion_hasta = None
                reserva.save(update_fields=['estado', 'retencion_hasta'])
                messages.success(request, 'Tu reserva fue cancelada. La habitación vuelve a estar disponible para esas fechas.')
            else:
                messages.error(request, 'Esta reserva ya no admite cancelación en línea. Contacta a recepción.')
    except OperationalError:
        messages.error(request, 'La reserva está siendo actualizada. Inténtalo nuevamente.')
    destino = reverse('hotel:mis_reservas')
    if request.user.is_authenticated:
        destino += '?reserva=' + codigo
    return redirect(destino)
