from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path
from . import views
from . import checkout_views
from . import gestion_views

app_name = 'hotel'
urlpatterns = [
    path('', views.home, name='home'),
    path('disponibilidad/', views.fechas, name='fechas'),
    path('habitaciones/', views.habitaciones, name='habitaciones'),
    path('reserva/seleccionar/', views.seleccionar_estadia, name='seleccionar'),
    path('reserva/pago/', checkout_views.pago, name='pago'),
    path('reserva/confirmacion/<str:codigo>/', checkout_views.confirmacion, name='confirmacion'),
    path('reserva/comprobante/<str:codigo>/', checkout_views.comprobante, name='comprobante'),
    path('mi-reserva/', gestion_views.mis_reservas, name='mis_reservas'),
    path('mi-reserva/<str:codigo>/cancelar/', gestion_views.cancelar_reserva, name='cancelar_reserva'),
    path('ingresar/', LoginView.as_view(template_name='hotel/cuenta.html', next_page='hotel:home', redirect_authenticated_user=True), name='ingresar'),
    path('registrarse/', views.registro, name='registro'),
    path('salir/', LogoutView.as_view(next_page='hotel:home'), name='salir'),
]
