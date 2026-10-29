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

from .models import Reserva
from .reservas import (
    ErrorReserva, RetencionVencida, confirmar_pago, liberar_retencion,
    preparar_pago, revision_pago,
)


class PagoPruebaForm(forms.Form):
    titular_nombre = forms.CharField(label='Nombre', max_length=100)
    titular_apellido = forms.CharField(label='Apellidos', max_length=100)
    titular_rut = forms.CharField(label='RUT', max_length=14)
    titular_telefono = forms.CharField(label='Teléfono de contacto', max_length=25)
    titular_correo = forms.EmailField(label='Correo de contacto')
    resultado_pago = forms.ChoiceField(label='Resultado del pago de prueba', choices=(
        ('APROBADO', 'Aprobar pago de prueba'),
        ('RECHAZADO', 'Simular pago rechazado'),
    ))
    acepta_condiciones = forms.BooleanField(label='He revisado mis datos, el total y las condiciones de reserva y cancelación.')
    revision = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, reserva, **kwargs):
        super().__init__(*args, **kwargs)
        if reserva.cliente_id:
            for campo in ('nombre', 'apellido', 'rut', 'telefono', 'correo'):
                self.fields[f'titular_{campo}'].disabled = True

    def clean_titular_rut(self):
        rut = self.cleaned_data['titular_rut'].strip().replace('.', '').replace(' ', '').upper()
        if len(rut) > 12:
            raise forms.ValidationError('Revisa el formato de tu RUT.')
        return rut

    def clean_titular_correo(self):
        return self.cleaned_data['titular_correo'].strip().lower()


def contexto_estadia(reserva):
    noches = reserva.contar_noches(reserva.fecha_entrada, reserva.fecha_salida)
    detalles = list(reserva.detalles.select_related('servicio'))
    alojamiento = reserva.precio_noche_pactado * noches
    subtotal = alojamiento + sum(d.subtotal for d in detalles)
    return {
        'reserva': reserva, 'noches': noches, 'detalles': detalles,
        'alojamiento': alojamiento, 'subtotal': subtotal,
        'limite_cancelacion': reserva.fecha_entrada - timedelta(hours=3),
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


def reserva_autorizada(request, codigo):
    reserva = get_object_or_404(Reserva.objects.select_related('habitacion', 'cliente__usuario'), codigo=codigo, estado__in=[Reserva.Estado.CONFIRMADA, Reserva.Estado.CANCELADA])
    propia = request.user.is_authenticated and reserva.cliente_id and reserva.cliente.usuario_id == request.user.pk
    if not propia and codigo not in request.session.get('reservas_autorizadas', []):
        raise Http404
    return reserva


@never_cache
def confirmacion(request, codigo):
    reserva = reserva_autorizada(request, codigo)
    if reserva.estado == Reserva.Estado.CANCELADA:
        return redirect(reverse('hotel:mis_reservas') + '?' + urlencode({'reserva': codigo, 'codigo': codigo}))
    contexto = contexto_estadia(reserva)
    contexto['paso'] = 5
    return render(request, 'hotel/confirmacion.html', contexto)


@never_cache
def comprobante(request, codigo):
    reserva = reserva_autorizada(request, codigo)
    datos = contexto_estadia(reserva)
    lineas = [
        'HOTEL GRAND CORDILLERA', 'Comprobante de reserva', '',
        f'Código: {reserva.codigo}', f'Estado: {reserva.get_estado_display()}',
        f'Titular: {reserva.titular_nombre} {reserva.titular_apellido}',
        f'RUT: {reserva.titular_rut}', f'Correo: {reserva.titular_correo}',
        f'Habitación: {reserva.habitacion.numero} - {reserva.habitacion.get_tipo_display()}',
        f'Huéspedes: {reserva.numero_huespedes}',
        f'Entrada: {timezone.localtime(reserva.fecha_entrada):%d/%m/%Y %H:%M}',
        f'Salida: {timezone.localtime(reserva.fecha_salida):%d/%m/%Y %H:%M}',
        f'Alojamiento: ${datos["alojamiento"]:,.2f} CLP',
    ]
    lineas += [f'{d.servicio.nombre} x {d.cantidad}: ${d.subtotal:,.2f} CLP' for d in datos['detalles']]
    lineas += [f'Descuento: ${reserva.descuento_aplicado:,.2f} CLP', f'Total: ${reserva.monto_total:,.2f} CLP']
    if reserva.pago_simulado:
        lineas += ['', 'PAGO DE PRUEBA APROBADO. No se realizó ningún cargo real.']
    lineas += ['', 'Este comprobante no constituye una boleta o factura.']
    respuesta = HttpResponse('\n'.join(lineas), content_type='text/plain; charset=utf-8')
    respuesta['Content-Disposition'] = f'attachment; filename="reserva-{reserva.codigo}.txt"'
    return respuesta
