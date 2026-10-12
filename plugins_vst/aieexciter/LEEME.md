# AIE Exciter — excitador armónico multibanda (estilo Ozone Exciter)

![AIE Exciter](captura.png)

Plugin VST 2.4 de 4 bandas que añade armónicos (calidez, brillo, presencia, "pegamento").

## Por banda
| Control | Qué hace |
|---|---|
| Perfil | Tube, Tape, Retro, Warm, Triode, Dual Triode, Warm Tape (curvas propias que buscan ese carácter) |
| Modo | Estéreo · Mid (solo el centro) · Side (solo los lados) · Transient (solo los ataques) · Sustain (solo el cuerpo) |
| Amount | cantidad de armónicos: 0 = sin efecto · 10 % ≈ -40 dB (sutil) · 30 % ≈ -25 dB · 100 % ≈ -10 dB (seno de prueba, Warm) |
| Mix | seco/húmedo (saturación paralela) |
| Solo / Bypass | escuchar solo la banda / dejarla limpia |
| Arm. | medidor de armónicos añadidos respecto a la banda (dB) |

Global: cortes arrastrables sobre el espectro (salida en gris y armónicos añadidos en color), Oversampling
x1/x2/x4/x8, Delta (escuchar solo lo añadido), Output, y la curva de la banda seleccionada (clic en su nombre).

## Cómo funciona
- Bandas con cruces Linkwitz-Riley de 4.º orden con compensación de fase (suma plana).
- **Independiente del nivel**: cada componente se divide por su envolvente antes de saturar y se vuelve a
  multiplicar después. Un pasaje flojo y uno fuerte reciben la misma proporción de armónicos y el volumen no
  cambia (medido: ±0,2 dB a -36, -12 y -3 dBFS con Amount 100 %).
- Sobremuestreo con las mismas etapas de media banda que AIE Clip; latencia fija de 69 muestras (Cubase la
  compensa) con cualquier factor. Con un seno de 7 kHz muy saturado: aliasing -25 dB a x1, -58 dB a x8.
- CPU medida (Linux x86, 4 bandas activas): ~3,6 % de un núcleo a x2, ~9 % a x8.

## Carácter medido (seno de 300 Hz, Amount 60 %)
| Perfil | H2 | H3 |
|---|---|---|
| Tube / Triode | ≈ -31 dB | ≈ -15 dB |
| Warm | ≈ -36 dB | ≈ -16 dB |
| Warm Tape / Dual Triode | ≈ -46/-49 dB | ≈ -15 dB |
| Tape / Retro | sin pares | ≈ -16 dB |

## Pruebas
`tests_exciter.cpp` (todo lo anterior más Delta, Mix 0, Mid/Side, Transient/Sustain, ratón y velocidad) y
`test_exciter_host.c` (lo carga como Cubase y en Windows abre la ventana real).
