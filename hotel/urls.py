from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path
from . import views
from . import checkout_views
from . import gestion_views
from . import panel_views

app_name = 'hotel'
urlpatterns = [
    path('', views.home, name='home'),
    path('disponibilidad/', views.fechas, name='fechas'),
    path('habitaciones/', views.habitaciones, name='habitaciones'),
    path('reserva/seleccionar/', views.seleccionar_estadia, name='seleccionar'),
    path('reserva/pago/', checkout_views.pago, name='pago'),
    path('reserva/cupon/', checkout_views.aplicar_cupon, name='aplicar_cupon'),
    path('panel/', panel_views.dashboard, name='panel_dashboard'),
    path('panel/estado/', panel_views.estado_dashboard, name='panel_estado'),
    path('panel/reservas/', panel_views.reservas, name='panel_reservas'),
    path('panel/reservas/<int:pk>/', panel_views.detalle_reserva, name='panel_reserva'),
    path('panel/reservas/<int:pk>/accion/', panel_views.accion_reserva, name='panel_accion'),
    path('panel/habitaciones/<int:pk>/estado/', panel_views.estado_habitacion, name='panel_habitacion'),
    path('panel/clientes/', panel_views.clientes, name='panel_clientes'),
    path('panel/promociones/', panel_views.promociones, name='panel_promociones'),
    path('panel/promociones/<int:pk>/', panel_views.campana, name='panel_campana'),
    path('panel/promociones/<int:pk>/reintentar/', panel_views.reintentar_campana, name='panel_reintentar'),
    path('panel/correos/<int:pk>/', panel_views.correo_promocion, name='panel_correo'),
    path('panel/auditoria/', panel_views.auditoria, name='panel_auditoria'),
    path('reserva/confirmacion/<str:codigo>/', checkout_views.confirmacion, name='confirmacion'),
    path('reserva/comprobante/<str:codigo>/', checkout_views.comprobante, name='comprobante'),
    path('mi-reserva/', gestion_views.mis_reservas, name='mis_reservas'),
    path('mi-reserva/<str:codigo>/cancelar/', gestion_views.cancelar_reserva, name='cancelar_reserva'),
    path('ingresar/', LoginView.as_view(template_name='hotel/cuenta.html', next_page='hotel:home', redirect_authenticated_user=True), name='ingresar'),
    path('registrarse/', views.registro, name='registro'),
    path('salir/', LogoutView.as_view(next_page='hotel:home'), name='salir'),
]
