from datetime import timedelta
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.db import OperationalError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .campos import TelefonoMovilField
from .comprobantes import extras_facturados, texto_comprobante
from .models import Reserva
from .validaciones import validar_arroba, validar_rut
from .reservas import (
    ErrorReserva, ReservaDeOtraSesion, RetencionVencida, confirmar_pago, liberar_retencion,
    preparar_pago, revision_pago,
    aplicar_cupon_reserva,
)


class PagoPruebaForm(forms.Form):
    titular_nombre = forms.CharField(label='Nombre', max_length=100)
    titular_apellido = forms.CharField(label='Apellidos', max_length=100)
    titular_rut = forms.CharField(label='RUT', max_length=20)
    titular_telefono = TelefonoMovilField(label='Teléfono de contacto')
    titular_correo = forms.CharField(
        label='Correo de contacto', max_length=254, validators=[validar_arroba],
        widget=forms.TextInput(attrs={'inputmode': 'email', 'autocomplete': 'email'}),
    )
    resultado_pago = forms.ChoiceField(label='Resultado del pago de prueba', choices=(
        ('APROBADO', 'Aprobar pago de prueba'),
        ('RECHAZADO', 'Simular pago rechazado'),
    ))
    acepta_condiciones = forms.BooleanField(label='He revisado mis datos, el total y las condiciones de reserva y cancelación.')
    revision = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, reserva, **kwargs):
        super().__init__(*args, **kwargs)
        if reserva.cliente_id:
            self.fields['titular_telefono'] = forms.CharField(
                label='Teléfono de contacto', max_length=25, required=False,
            )
            for campo in ('nombre', 'apellido', 'rut', 'telefono', 'correo'):
                self.fields[f'titular_{campo}'].disabled = True

    def clean_titular_rut(self):
        rut = self.cleaned_data['titular_rut']
        if self.fields['titular_rut'].disabled:
            return rut
        return validar_rut(rut)

    def clean_titular_correo(self):
        return self.cleaned_data['titular_correo'].strip().lower()


def contexto_estadia(reserva):
    noches = reserva.contar_noches(reserva.fecha_entrada, reserva.fecha_salida)
    detalles = extras_facturados(reserva)
    alojamiento = reserva.precio_noche_pactado * noches
    subtotal = alojamiento + sum(d.subtotal for d in detalles)
    return {
        'reserva': reserva, 'noches': noches, 'detalles': detalles,
        'alojamiento': alojamiento, 'subtotal': subtotal,
        'limite_cancelacion': reserva.fecha_entrada - timedelta(hours=3),
        'cupon_aplicado': reserva.cupones.filter(estado__in=['ACTIVO', 'USADO']).first(),
    }


def volver_a_habitaciones(request, mensaje):
    messages.error(request, mensaje)
    seleccion = request.session.get('seleccion_estadia', {})
    consulta = {k: seleccion[k] for k in ('entrada', 'salida', 'huespedes') if k in seleccion}
    return redirect(reverse('hotel:habitaciones') + '?' + urlencode(consulta))


@never_cache
def pago(request):
    seleccion = request.session.get('seleccion_estadia')
    if not seleccion:
        messages.info(request, 'Elige primero las fechas y una habitación.')
        return redirect('hotel:fechas')
    reserva_id = request.session.get('reserva_en_curso')
    # Una petición POST siempre usa la retención ya emitida; no crea otra.
    if request.method == 'POST' and not reserva_id:
        return volver_a_habitaciones(request, 'La retención ya no está disponible. Selecciona nuevamente tu habitación.')
    try:
        reserva = preparar_pago(
            seleccion, request.user if request.user.is_authenticated else None,
            reserva_id if request.session.get('reserva_seleccion') == seleccion else None,
        )
    except RetencionVencida as error:
        liberar_retencion(reserva_id)
        request.session.pop('reserva_en_curso', None)
        return volver_a_habitaciones(request, str(error))
    except ReservaDeOtraSesion as error:
        request.session.pop('reserva_en_curso', None)
        request.session.pop('reserva_seleccion', None)
        return volver_a_habitaciones(request, str(error))
    except ErrorReserva as error:
        return volver_a_habitaciones(request, str(error))
    except OperationalError:
        return volver_a_habitaciones(request, 'Otra reserva está siendo procesada. Vuelve a intentarlo.')
    request.session['reserva_en_curso'] = reserva.pk
    request.session['reserva_seleccion'] = seleccion
    if reserva.estado == Reserva.Estado.CONFIRMADA:
        autorizadas = request.session.get('reservas_autorizadas', [])
        if reserva.codigo not in autorizadas:
            request.session['reservas_autorizadas'] = (autorizadas + [reserva.codigo])[-20:]
        return redirect('hotel:confirmacion', codigo=reserva.codigo)
    inicial = {f'titular_{campo}': getattr(reserva, f'titular_{campo}') for campo in ('nombre', 'apellido', 'rut', 'telefono', 'correo')}
    inicial['revision'] = revision_pago(reserva)
    form = PagoPruebaForm(request.POST if request.method == 'POST' else None, initial=inicial, reserva=reserva)
    if request.method == 'POST' and form.is_valid():
        try:
            reserva, error = confirmar_pago(reserva.pk, form.cleaned_data, form.cleaned_data['revision'])
        except RetencionVencida as error:
            liberar_retencion(reserva.pk)
            request.session.pop('reserva_en_curso', None)
            return volver_a_habitaciones(request, str(error))
        except (ErrorReserva, OperationalError) as error:
            form.add_error(None, str(error) if isinstance(error, ErrorReserva) else 'Otra reserva está siendo procesada. Vuelve a intentarlo.')
        else:
            if error:
                form.add_error(None, error)
                # Emitir una nueva revisión después de un cambio de importe.
                datos_form = form.data.copy()
                datos_form['revision'] = revision_pago(reserva)
                form.data = datos_form
            else:
                autorizadas = request.session.get('reservas_autorizadas', [])
                if reserva.codigo not in autorizadas:
                    request.session['reservas_autorizadas'] = (autorizadas + [reserva.codigo])[-20:]
                return redirect('hotel:confirmacion', codigo=reserva.codigo)
    contexto = contexto_estadia(reserva)
    contexto.update({'form': form, 'paso': 4, 'ahora': timezone.now()})
    return render(request, 'hotel/pago.html', contexto)


@require_POST
@never_cache
def aplicar_cupon(request):
    identificador = request.session.get('reserva_en_curso')
    if not identificador:
        return redirect('hotel:fechas')
    try:
        aplicar_cupon_reserva(identificador, request.user, request.POST.get('codigo_cupon',''))
    except (ErrorReserva, Reserva.DoesNotExist) as error:
        messages.error(request, str(error))
    else:
        messages.success(request, 'Descuento actualizado. Revisa el total antes de confirmar.')
    return redirect('hotel:pago')


def reserva_autorizada(request, codigo):
    reserva = get_object_or_404(Reserva.objects.select_related('habitacion', 'cliente__usuario'), codigo=codigo, estado__in=[Reserva.Estado.CONFIRMADA, Reserva.Estado.EN_ESTADIA, Reserva.Estado.COMPLETADA, Reserva.Estado.CANCELADA])
    if request.user.is_authenticated:
        if not reserva.cliente_id or reserva.cliente.usuario_id != request.user.pk:
            raise Http404
    elif codigo not in request.session.get('reservas_autorizadas', []):
        from .gestion_views import consulta_publica_vigente
        if not consulta_publica_vigente(request, reserva):
            raise Http404
    return reserva


@never_cache
def confirmacion(request, codigo):
    reserva = reserva_autorizada(request, codigo)
    if reserva.estado != Reserva.Estado.CONFIRMADA:
        return redirect(reverse('hotel:mis_reservas') + '?' + urlencode({'reserva': codigo, 'codigo': codigo}))
    contexto = contexto_estadia(reserva)
    contexto['paso'] = 5
    return render(request, 'hotel/confirmacion.html', contexto)


@never_cache
def comprobante(request, codigo):
    reserva = reserva_autorizada(request, codigo)
    datos = contexto_estadia(reserva)
    texto = texto_comprobante(reserva, datos["alojamiento"], datos["detalles"])
    respuesta = HttpResponse(texto, content_type='text/plain; charset=utf-8')
    respuesta['Content-Disposition'] = f'attachment; filename="reserva-{reserva.codigo}.txt"'
    return respuesta
