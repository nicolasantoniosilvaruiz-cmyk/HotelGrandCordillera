import re

from django import forms


CODIGOS_TELEFONO = (('+56', '+56'),)


class TelefonoMovilWidget(forms.MultiWidget):
    template_name = 'hotel/telefono_movil.html'

    def __init__(self):
        super().__init__({
            'pais': forms.Select(choices=CODIGOS_TELEFONO, attrs={'aria-label': 'Código de país'}),
            'numero': forms.TextInput(attrs={
                'aria-label': 'Número de celular de ocho dígitos',
                'inputmode': 'numeric',
                'autocomplete': 'tel-local',
                'maxlength': '8',
                'pattern': '[0-9]{8}',
                'placeholder': '12345678',
            }),
        })

    def id_for_label(self, id_):
        return f'{id_}_0' if id_ else ''

    def decompress(self, value):
        compacto = re.sub(r'\s+', '', value or '')
        coincidencia = re.fullmatch(r'(?:\+56)?9([0-9]{8})', compacto)
        return ['+56', coincidencia[1] if coincidencia else '']


class TelefonoMovilField(forms.MultiValueField):
    def __init__(self, **kwargs):
        mensaje = 'Ingresa los ocho dígitos del celular después del 9.'
        super().__init__(
            fields=(
                forms.ChoiceField(choices=CODIGOS_TELEFONO),
                forms.RegexField(r'\A[0-9]{8}\Z', strip=False, error_messages={'invalid': mensaje}),
            ),
            widget=TelefonoMovilWidget(),
            error_messages={'required': mensaje},
            **kwargs,
        )

    def compress(self, data_list):
        if data_list:
            codigo, numero = data_list
            return f'{codigo}9{numero}'
        return ''
