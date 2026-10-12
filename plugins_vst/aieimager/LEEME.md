# AIE Imager — imagen estéreo multibanda (estilo Ozone Imager)

![AIE Imager](captura.png)

Plugin VST 2.4 de 4 bandas para controlar la anchura estéreo por frecuencias, sin latencia.

## Cómo funciona
- Trabaja en **Mid/Side**. Solo se cambia la parte lateral (Side) de cada banda; el centro (Mid) recibe los
  mismos filtros pero no se modifica, así que **la suma mono es la misma con cualquier ajuste**.
- Bandas con un árbol de cruces Linkwitz-Riley de 4.º orden con compensación de fase: con todo al 100 % cada
  frecuencia sale al mismo nivel (desviación < 0,05 dB medida) y con la misma imagen estéreo.

## Controles (versión 1.1, al estilo de Ozone 11)
| Control | Qué hace |
|---|---|
| Espectro estéreo | espectro en tiempo real del centro (gris) y de los lados (color de cada banda) |
| Cortes (asas sobre el espectro) | frecuencias entre bandas (por defecto 120 Hz, 1 kHz, 6 kHz); se ordenan solos |
| Width por banda | escala de Ozone: -100 = mono · 0 = sin cambios · +100 = el doble de anchura |
| Corr por banda | correlación de la banda con límites según su frecuencia: en graves el verde exige casi mono (0,9), en agudos admite mucha anchura (0,1); amarillo = al límite, rojo = problema |
| Stereoize por banda + modo I / II | abre bandas mono sin cambiar la suma mono. I: retardo corto (Haas). II: decorrelado con pasa-todo (más suave, sin eco) |
| Solo / Bypass por banda | escuchar solo esa banda / dejarla sin procesar |
| Link | al mover un Width se mueven todos lo mismo |
| Vectorscopio | Polar Sample, Polar Level o Lissajous |
| Mono | escuchar la mezcla en mono para comprobar |
| Reiniciar | todas las bandas sin cambios |

Los 16 primeros parámetros son los de la versión 1.0 en el mismo orden: los proyectos guardados siguen cargando.

Uso típico en EDM: banda 1 (graves) a -100 para un bajo mono y firme; bandas 3–4 a +20/+50 sin pasar a
amarillo para abrir sintes y aire; Stereoize II suave en medios si un sinte mono suena estrecho.

## Pruebas
- `tests_imager.cpp`: nivel plano con todo al 100 %, sin latencia, suma mono intacta con ajustes extremos,
  Width 0 % deja la banda en mono, Width 200 % duplica el lateral, Stereoize abre una banda mono sin cambiar
  la suma mono, Solo aísla la banda, Mono, cortes cruzados, ratón y velocidad de dibujo.
- `test_imager_host.c`: lo carga como Cubase; en Windows abre la ventana real, hace clic y la captura.
