# Medir Ozone para igualar AIE Exciter y AIE Imager

La señal (`senal_prueba_ozone.wav`, ~66 s) repite cada medida tres veces en momentos distintos: **sirven las
versiones de demostración que meten silencios**; `analizar.py` detecta los silencios y usa las copias enteras.

## Qué hacer
1. Abre `senal_prueba_ozone.wav` en **Ozone** (7 u 11): como plugin en una pista de Cubase (Ozone 7 tiene
   versión de 32 bits) o en la aplicación independiente de Ozone. Cubase 5 de 32 bits no carga Ozone 11.
2. Desactiva **todos** los módulos menos el que se mide. Ganancia de entrada y salida a 0 dB. Sin dither.
3. Exporta cada ajuste de la lista como **WAV 44,1 kHz, 32 bits coma flotante** (o 24 bits), estéreo,
   el archivo entero (desde 0 s hasta el final, sin recortar el principio).
4. Pon a cada archivo el nombre exacto de la lista.

## Exciter (módulo Exciter solo)
Interruptor **Multiband apagado** (una sola banda) o las cuatro bandas iguales, **Mix 100 %**, **Oversampling
activado** si existe, modo normal (ni Mid/Side ni Transient). Si tu versión no tiene algún perfil (Ozone 7 puede
no tener Dual Triode o Warm Tape), sáltalo.

| Archivo | Perfil | Amount |
|---|---|---|
| `exciter_tube_50.wav` | Tube | 50 |
| `exciter_tape_50.wav` | Tape | 50 |
| `exciter_retro_50.wav` | Retro | 50 |
| `exciter_warm_50.wav` | Warm | 50 |
| `exciter_triode_50.wav` | Triode | 50 |
| `exciter_dualtriode_50.wav` | Dual Triode | 50 |
| `exciter_warmtape_50.wav` | Warm Tape | 50 |
| `exciter_warm_20.wav` | Warm | 20 |
| `exciter_warm_100.wav` | Warm | 100 |

## Imager (módulo Imager solo)
Las cuatro bandas con el mismo valor.

| Archivo | Ajuste |
|---|---|
| `imager_width_menos50.wav` | Width -50 en todas las bandas |
| `imager_width_mas50.wav` | Width +50 en todas las bandas |
| `imager_stereoize1_50.wav` | Width 0, Stereoize I (modo I) a 50 en todas las bandas |
| `imager_stereoize2_50.wav` | Width 0, Stereoize II a 50 en todas las bandas |

## Cómo se analiza
`python analizar.py exciter_tube_50.wav ...` imprime latencia, ganancia y armónicos H2-H7 de cada seno, respuesta
en frecuencia, cambio de Mid/Side en los ruidos e intermodulación, y guarda `*_medidas.json`.

## Qué mido con cada archivo
- Armónicos (H2...H7) de 100 Hz, 1 kHz y 5 kHz a -24, -12, -6 y 0 dBFS: carácter de cada perfil y cómo cambia
  con el nivel y con la frecuencia.
- Barrido: respuesta en frecuencia (si Ozone cambia el tono además de añadir armónicos).
- Impulso: latencia y fase. Golpes de batería: cómo trata los transitorios.
- Ruido mono / lateral / estéreo: cuánto abre o cierra el Imager y si toca la suma mono.
- Dos tonos: intermodulación (lo "sucio" o limpio de la saturación).

Con esas medidas ajusto las curvas de AIE Exciter y AIE Imager para que se parezcan lo más posible y te digo
con números cuánto se parecen.
