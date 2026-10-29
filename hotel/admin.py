from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserCreationForm, UserChangeForm

from .models import (
    Usuario,
    Cliente,
    PersonalHotel,
    Habitacion,
    ServicioAdicional,
    Reserva,
    DetalleReserva,
    CuponDescuento,
    BitacoraReserva,
)


class CrearUsuarioForm(UserCreationForm):
    class Meta:
        model = Usuario
        fields = (
            "email",
            "rut",
            "nombre",
            "apellido",
            "telefono",
        )


class EditarUsuarioForm(UserChangeForm):
    class Meta:
        model = Usuario
        fields = "__all__"


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    add_form = CrearUsuarioForm
    form = EditarUsuarioForm
    model = Usuario

    list_display = (
        "email",
        "rut",
        "nombre",
        "apellido",
        "is_staff",
        "is_active",
    )
    list_filter = ("is_staff", "is_superuser", "is_active")
    search_fields = ("email", "rut", "nombre", "apellido")
    ordering = ("apellido", "nombre")
    readonly_fields = ("last_login", "date_joined")

    fieldsets = (
        ("Acceso", {
            "fields": ("email", "password"),
        }),
        ("Datos personales", {
            "fields": ("rut", "nombre", "apellido", "telefono"),
        }),
        ("Permisos", {
            "fields": (
                "is_active",
                "is_staff",
                "is_superuser",
                "groups",
                "user_permissions",
            ),
        }),
        ("Fechas", {
            "fields": ("last_login", "date_joined"),
        }),
    )

    add_fieldsets = (
        ("Crear usuario", {
            "classes": ("wide",),
            "fields": (
                "email",
                "rut",
                "nombre",
                "apellido",
                "telefono",
                "password1",
                "password2",
            ),
        }),
    )

    filter_horizontal = ("groups", "user_permissions")


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("id", "usuario")
    search_fields = (
        "usuario__rut",
        "usuario__email",
        "usuario__nombre",
        "usuario__apellido",
    )
    autocomplete_fields = ("usuario",)


@admin.register(PersonalHotel)
class PersonalHotelAdmin(admin.ModelAdmin):
    list_display = ("codigo_empleado", "usuario", "rol")
    list_filter = ("rol",)
    search_fields = (
        "codigo_empleado",
        "usuario__rut",
        "usuario__nombre",
        "usuario__apellido",
    )
    autocomplete_fields = ("usuario",)


@admin.register(Habitacion)
class HabitacionAdmin(admin.ModelAdmin):
    list_display = (
        "numero",
        "tipo",
        "precio_base_noche",
        "estado_actual",
    )
    list_filter = ("tipo", "estado_actual")
    search_fields = ("numero",)
    ordering = ("numero",)


@admin.register(ServicioAdicional)
class ServicioAdicionalAdmin(admin.ModelAdmin):
    list_display = ("nombre", "precio", "activo")
    list_filter = ("activo",)
    search_fields = ("nombre",)


class DetalleReservaInline(admin.TabularInline):
    model = DetalleReserva
    extra = 0
    autocomplete_fields = ("servicio",)
    fields = (
        "servicio",
        "cantidad",
        "precio_unitario",
        "observaciones",
    )


@admin.register(Reserva)
class ReservaAdmin(admin.ModelAdmin):
    list_display = (
        "codigo",
        "habitacion",
        "cliente",
        "fecha_entrada",
        "fecha_salida",
        "estado",
        "monto_total",
    )
    list_filter = ("estado", "fecha_entrada")
    search_fields = (
        "codigo",
        "titular_rut",
        "titular_nombre",
        "titular_apellido",
        "cliente__usuario__rut",
        "cliente__usuario__email",
    )
    autocomplete_fields = ("cliente", "personal", "habitacion")
    readonly_fields = (
        "codigo",
        "fecha_creacion",
        "precio_noche_pactado",
        "monto_total",
        "pago_simulado",
    )
    inlines = (DetalleReservaInline,)
    date_hierarchy = "fecha_entrada"

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        # Los servicios del formulario se guardan después de la reserva.
        form.instance.recalcular_total()

    def get_readonly_fields(self, request, obj=None):
        campos = list(super().get_readonly_fields(request, obj))
        if request.method == "POST":
            tiene_cliente = bool(request.POST.get("cliente"))
        else:
            tiene_cliente = obj is not None and obj.cliente_id is not None

        if tiene_cliente:
            campos.extend([
                "titular_rut",
                "titular_nombre",
                "titular_apellido",
                "titular_correo",
                "titular_telefono",
            ])
        return campos

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name.startswith("titular_"):
            kwargs["help_text"] = (
                "Completar solo para invitados. Si seleccionas un cliente, "
                "sus datos se copiarán automáticamente al guardar."
            )
        return super().formfield_for_dbfield(db_field, request, **kwargs)


@admin.register(DetalleReserva)
class DetalleReservaAdmin(admin.ModelAdmin):
    list_display = (
        "reserva",
        "servicio",
        "cantidad",
        "precio_unitario",
        "subtotal",
    )
    search_fields = ("reserva__codigo", "servicio__nombre")
    autocomplete_fields = ("reserva", "servicio")


@admin.register(CuponDescuento)
class CuponDescuentoAdmin(admin.ModelAdmin):
    list_display = (
        "codigo",
        "cliente",
        "porcentaje",
        "fecha_vencimiento",
        "estado",
        "reserva",
    )
    list_filter = ("estado", "fecha_vencimiento")
    search_fields = (
        "codigo",
        "cliente__usuario__rut",
        "cliente__usuario__email",
    )
    autocomplete_fields = ("cliente", "reserva")


@admin.register(BitacoraReserva)
class BitacoraReservaAdmin(admin.ModelAdmin):
    list_display = ("reserva", "personal", "fecha", "accion")
    list_filter = ("accion", "fecha")
    search_fields = ("reserva__codigo", "motivo")
    autocomplete_fields = ("reserva", "personal")


admin.site.site_header = "Administración Hotel Grand Cordillera"
admin.site.site_title = "Hotel Grand Cordillera"
admin.site.index_title = "Gestión del hotel"
