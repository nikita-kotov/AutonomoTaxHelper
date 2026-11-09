# Autonomo Tax Helper

[English](README.md) · [Русский](README.ru.md) · **Español**

[![Tests](https://github.com/nikita-kotov/AutonomoTaxHelper/actions/workflows/tests.yml/badge.svg)](https://github.com/nikita-kotov/AutonomoTaxHelper/actions/workflows/tests.yml)

Aplicación web local para autónomos: contabilidad de ingresos y gastos, cálculo del **modelo 303** (IVA), del **modelo 130** (IRPF), datos para la **Renta (modelo 100)** y un seguimiento de declaraciones con plazos y estados.
El servidor escucha solo en `127.0.0.1` y los datos se quedan en su ordenador. La interfaz está disponible en español, inglés y ruso.

## Contenido

- [Funciones](#funciones)
- [Alcance y estado](#alcance-y-estado)
- [Instalación e inicio](#instalación-e-inicio)
- [Guías](#guías)
- [Qué se configura para cada autónomo](#qué-se-configura-para-cada-autónomo)
- [Datos, privacidad y copias de seguridad](#datos-privacidad-y-copias-de-seguridad)
- [Idiomas de la interfaz](#idiomas-de-la-interfaz)
- [Desarrollo](#desarrollo)
- [Apoyar al autor](#apoyar-al-autor)
- [Licencia](#licencia)
- [Exención de responsabilidad](#exención-de-responsabilidad)

## Funciones

| Área | Contenido |
| --- | --- |
| Panel | Cifras del año, gráfico por trimestres (ingresos, gastos deducibles, IRPF, IVA) y estimación del resultado de la Renta: IRPF según la escala general menos retenciones y pagos trimestrales |
| Operaciones | Diario de facturas emitidas y recibidas; adjuntos (PDF e imágenes se abren en el navegador, el resto se descarga); facturas en cualquier divisa con el tipo del BCE; copia de un apunte para suscripciones mensuales; importación de CSV con vista previa y exportación a CSV |
| Contactos | Clientes y proveedores con país, NIF, dirección, correo y valores por defecto (tratamiento del IVA, categoría, tipos); una operación nueva parte del contacto guardado |
| Libros para la AEAT | Un archivo Excel con los libros de ingresos y gastos (libros registro) en el formato de la AEAT, validado en el servicio de validación de la AEAT; Renta WEB, el formulario del modelo 130 y Pre303 lo importan y rellenan sus casillas |
| 303 | IVA devengado y deducible por casilla, inversión del sujeto pasivo (UE y fuera de la UE), facturas rectificativas (casillas 14–15 y 40–41), compensación de un resultado negativo |
| 130 | Cálculo acumulado de las casillas 01–07 y 12–19, gastos de difícil justificación, retenciones, minoración |
| 115 | Opcional: IRPF que usted retiene al arrendador de un local, por trimestre: perceptores, base, retenciones e importe a ingresar |
| Renta | Ingresos, gastos por categoría, rendimiento neto, reducción opcional del 20 % por inicio de actividad, retenciones y pagos fraccionados ingresados |
| Declaraciones | Calendario de 303, 130, 390, Renta y modelos propios; estados presentado / pendiente / vencido, fecha, justificante, PDF; resumen en el panel; restauración de las entradas estándar eliminadas |
| Ficheros AEAT | Generación de los ficheros 130 y 303 en el formato oficial, validación en el servicio de la AEAT y envío al entorno de pruebas de la AEAT |
| Ajustes y categorías | Perfil del contribuyente, reglas de deducción, límites anuales por categoría, copias de seguridad |

## Alcance y estado

**Alcance.** IVA régimen general, periodos trimestrales, estimación directa, territorio común.

**Estado.** Versión temprana. Los ficheros del 130 y del 303 se generan y se validan en el servicio de la AEAT. El envío a la AEAT está implementado solo para el entorno de pruebas: está cubierto por tests, pero todavía no se ha probado en el servidor de pruebas real, y el envío al entorno de producción no está activado.

## Instalación e inicio

**Requisitos.** Python 3.12. `run.bat` (Windows) y `run.sh` (macOS, Linux) crean el entorno virtual e instalan los paquetes en el primer inicio. Firmar declaraciones con certificado (la página Presentación) solo funciona en Windows.

Doble clic en `run.bat`: la aplicación arranca en segundo plano con un icono en la bandeja del sistema (sin ventana de consola) y se abre en el navegador en <http://127.0.0.1:5057/>. Doble clic en el icono vuelve a abrir la página; su menú (clic derecho) también abre la carpeta de datos y detiene la aplicación. **Salir** en la barra superior de la página la detiene igualmente. Si ya está en marcha, `run.bat` solo abre la página. Los problemas de arranque se escriben en `app.log` en la carpeta de datos.

En macOS y Linux ejecute `./run.sh` en una terminal (o `sh run.sh`): la aplicación funciona en esa consola, abre la misma dirección en el navegador y se detiene con **Salir** en la página o con Ctrl+C. Probado en Windows.

Primeros pasos en la aplicación: **Ajustes** → NIF/NIE, apellidos y nombre (tal como figuran en el censo de la AEAT), fecha de inicio de la actividad, forma de pago, porcentaje de afectación de la vivienda del modelo 036 → **Operaciones** → primer gasto.

## Guías

### Introducción de importes

Escriba los importes como lo hace el idioma de la interfaz: `1.234,56` (ruso, español) o `1,234.56` (inglés). Si hay un solo separador seguido de exactamente tres cifras (`1.500`), se interpreta como separador de miles cuando no coincide con el signo decimal de su idioma: en la interfaz en español `1.500` significa 1 500 €, y `1,5` significa 1,50 €. Con dos separadores distintos no hay ambigüedad: el último es el decimal.

### Clientes y proveedores

**Contactos** guarda las empresas y personas a las que factura o a las que compra: nombre, país, NIF, dirección, correo y los valores de los que debe partir una operación nueva (tratamiento del IVA, categoría, tipo de IVA, retención de IRPF, descripción). En el formulario de la operación, al escribir un nombre guardado se rellenan esos campos, salvo los que ya haya cambiado a mano. Una operación nueva puede guardar su contraparte como contacto con una casilla, **Añadir desde operaciones** crea un contacto por cada nombre que ya está en el libro, y la importación de CSV toma las celdas vacías del contacto guardado con el mismo nombre.

### Importación de operaciones desde CSV

**Operaciones → Importar CSV** acepta un archivo con una fila por factura y muestra una vista previa antes de guardar nada: cada fila se marca como lista, duplicada (misma fecha, contraparte, número e importe que una operación existente o una fila anterior) o con error y su motivo, y usted marca las filas que se importan. Las filas se validan con las mismas reglas que el formulario, la importación es todo o nada y a las filas en divisa extranjera se les asigna el tipo del BCE de la fecha de la factura.

Columnas obligatorias: `date` y `base` (o `orig_amount` con `currency`); las demás son opcionales y las celdas vacías toman los valores de Ajustes: `kind` (expense o income), `number`, `counterparty`, `country`, `vat_id`, `description`, `category` (código o nombre, obligatoria en gastos), `regime`, `vat_rate`, `vat_amount`, `withholding_rate`, `notes`. Fechas `AAAA-MM-DD` o `DD/MM/AAAA`, importes como `1234.56`, separador `;`, `,` o tabulador. En la página de importación se descarga una plantilla, y el CSV que exporta la propia aplicación se puede volver a leer.

### Facturas en divisa extranjera

Para una divisa distinta del EUR, el formulario de la operación muestra «Importe en la divisa de la factura» y «Cambio (unidades por 1 EUR)». La base en euros es importe ÷ cambio, redondeada al céntimo, y interviene en todos los cálculos fiscales. Puede corregirla a mano: si difiere de la conversión, la aplicación avisa y conserva lo que escribió.

- **Origen del tipo.** El botón «Obtener el tipo del BCE a la fecha de la factura» (también automático al cambiar divisa o fecha) rellena el tipo de referencia del BCE. En fines de semana y festivos se usa el último día publicado y se guarda su fecha. Los tipos obtenidos se almacenan en la base de datos. En modo manual (Ajustes) el tipo se introduce a mano.
- **Qué se guarda:** divisa, importe en divisa, tipo, fecha del tipo y origen (`ecb` o `manual`); todo es visible en la lista de operaciones y en el CSV.
- **Base.** Para el IVA (art. 79 LIVA) la base se convierte al tipo vendedor del Banco de España en la fecha del devengo, normalmente la de la factura. El Banco de España publica su tipo a partir del tipo de referencia del BCE, por lo que las cifras suelen coincidir.

### Oficina en casa y suscripciones

- **Suscripciones de IA y SaaS** van a la categoría «Software y suscripciones», 100 % gasto de IRPF. El IVA depende del régimen de la factura:
  - proveedor español con IVA → «Proveedor español», el IVA se deduce;
  - proveedor de la UE sin IVA → «Inversión del sujeto pasivo UE»: 303 casillas 10/11 y 36/37, y además se presenta el modelo 349;
  - proveedor de fuera de la UE sin IVA → «Inversión del sujeto pasivo no UE»: casillas 12/13 y 28/29;
  - IVA extranjero en la factura → «Sin IVA»: todo el importe va a base y a gasto.
- **El porcentaje de afectación** se introduce exactamente como en el modelo 036 (casilla 423, por ejemplo 11 %); la superficie de la vivienda (casilla 422) se guarda como referencia.
- **Las reglas de IRPF se eligen por separado para dos grupos** (Ajustes → Oficina en casa; el multiplicador del 30 % es común por defecto):
  - suministros (luz, gas, agua, internet): por defecto coste × % × 30 % (art. 30.2.5ª LIRPF); la opción «solo %» sirve si se puede acreditar una proporción mayor;
  - alquiler, IBI, comunidad, seguro del hogar: por defecto coste × % (el porcentaje del modelo 036), lo que permite la ley; la opción «coste × % × 30 %» es el enfoque prudente de muchos gestores.
- El grupo lo determina el modo de IRPF de la categoría; las categorías propias pueden pertenecer a cualquiera de los dos.
- **IVA de suministros** es un ajuste aparte: por defecto IVA × % de afectación, con la opción «IVA × % de afectación × 30 %».
- El IVA no recuperable se incluye en el gasto de IRPF.

### Categorías de gastos

Cada gasto pertenece a una categoría, y la categoría decide cómo llega a las cifras fiscales: la regla de IRPF (íntegro, suministros de la vivienda, alquiler e IBI de la vivienda o no deducible), el tratamiento del IVA, un límite anual de deducción opcional (por ejemplo, seguro médico, 500 € por persona) y el concepto de gasto en los libros de la AEAT. Las categorías estándar cubren los gastos habituales de un autónomo, así que la mayoría no tiene que cambiarlas. En **Ajustes → Categorías de gastos** puede fijar un límite, cambiar una regla o añadir una categoría propia: tendrá su propia línea en el desglose de la Renta y en los libros de la AEAT aparecerá como otro gasto deducible.

### Ficheros para la AEAT y validación

En las páginas **303** y **130** hay una tarjeta **«Fichero para la AEAT y validación»**:

1. La aplicación genera el fichero de longitud fija según el *diseño de registro* oficial (130: «Ejercicios 2019 y siguientes», 303: «Ejercicio 2026 y siguientes»).
2. El botón «Validar el archivo en el servicio de la AEAT» lo envía a `ServValiDos`. La respuesta es un PDF con el formulario prerrellenado o una lista de errores con códigos.
3. «Descargar fichero» entrega `NIF-año-periodo.130` o `.303` para subirlo a la Sede.

El resultado de la validación está ligado al contenido del fichero: si cambian los datos, la tarjeta lo marca como desactualizado. La validación necesita NIF/NIE, apellidos y nombre en Ajustes tal como figuran en el censo de la AEAT.

El fichero 303 contiene las páginas 01 (régimen general), 03 (información adicional y resultado) y DID (cuenta bancaria).

### Libros para Renta WEB, modelo 130 y Pre303

**Libros registro** genera los libros unificados de IVA e IRPF en un único archivo `.xlsx` con el formato publicado por la AEAT (hojas `EXPEDIDAS_INGRESOS` y `RECIBIDAS_GASTOS`, nombre del archivo `<año><NIF>T<nombre>.xlsx`). El mismo archivo lo importan:

- **Renta WEB**: Rendimientos de actividades económicas en estimación directa → *Importar Libros Registro* (todo el año, 4T). Rellena los ingresos, los gastos deducibles y las retenciones.
- **Modelo 130** y **Pre303 (303)**: el botón *Importar Libros Registro* del formulario, con las operaciones desde el 1 de enero hasta el final del trimestre (el trimestre se elige en la página).

El botón **Validar el archivo en el servicio de la AEAT** envía el archivo al validador de libros de la AEAT y muestra su veredicto y los periodos que acepta. Antes del primer uso, indique en **Ajustes** la clave de actividad y el grupo o epígrafe del IAE (el epígrafe sin el prefijo de sección: `763`, no `2*763`).

Cómo se convierten las operaciones en líneas de los libros: el gasto deducible es el importe que la aplicación ya usa para el 130 (con las proporciones de la oficina en casa y los límites por categoría); cada categoría corresponde a un concepto de gasto de la AEAT (por ejemplo servicios profesionales `G19`, Seguridad Social del titular `G45`, alquiler `G12`, electricidad `G14`); las compras con inversión del sujeto pasivo llevan el IVA autorrepercutido; los gastos sin IVA (Seguridad Social, recibos de alquiler) se registran como justificantes contables (`F6`); una factura cuyo IVA cubre solo una parte se divide en dos líneas. La página enumera lo que conviene completar antes de importar, por ejemplo un número de factura que falta o el identificador de un proveedor extranjero, que exige la AEAT.

### Presentación a la AEAT (entorno de pruebas)

A diferencia de la validación anterior, que solo comprueba un fichero y no necesita certificado, **Presentación** envía la declaración. Solo está disponible para el **entorno de pruebas** de la AEAT (`prewww1.aeat.es`), donde no tiene efecto legal.

- **Certificado.** Solo Windows. Elija su certificado digital del almacén de Windows en la página Presentación; Windows firma la conexión (y puede pedir el PIN). El titular del certificado debe ser el contribuyente de los ajustes.
- **Controles.** El archivo debe haberse validado en el servicio de la AEAT en su forma actual, el certificado debe estar vigente, y una pantalla de revisión muestra las cifras y exige una confirmación explícita. Una declaración con pago pide el NRC del pago.
- **Resultado.** La respuesta (código seguro de verificación, número de justificante, avisos o errores) y el PDF del justificante, si la AEAT lo ofrece, se guardan en la lista de declaraciones enviadas.
- **Reglas del entorno de pruebas.** Responde en días laborables de 8:00 a 15:00 (hora de España); tras una prueba correcta de un periodo, las siguientes pruebas de ese periodo deben ser declaraciones complementarias, así que use otro periodo para la siguiente prueba.

## Qué se configura para cada autónomo

| Dónde | Parámetro |
| --- | --- |
| Perfil fiscal | fecha de inicio de la actividad, método de IRPF (simplificada / normal), tipo del modelo 130 (20 %; 8 % para Ceuta y Melilla), alta en IVA, modelos a seguir, festivos propios |
| Contribuyente | NIF/NIE, apellidos y nombre, forma de pago (banco + NRC, o domiciliación + IBAN; con domiciliación los plazos son anteriores) |
| Valores por defecto | tipo de IVA y retención para nuevas operaciones; modo de tipos de cambio |
| Oficina en casa | porcentaje de afectación, superficie de la vivienda, multiplicador, reglas de suministros y alquiler, modo de IVA de suministros |
| IRPF | difícil justificación (porcentaje y límite), rendimiento del año anterior para la minoración, reducción opcional del 20 % por inicio de actividad, mínimo personal y escala autonómica para la estimación de la Renta, saldo de IVA a compensar |
| Ajustes → Categorías de gastos | modos de IRPF e IVA, límite anual de deducción (por ejemplo, seguro médico 500 € por persona), nombres, notas, categorías propias |
| Declaraciones | cualquier plazo se edita a mano y no se recalcula; modelos propios |

## Datos, privacidad y copias de seguridad

### Dónde están los datos

La base de datos y los documentos (`taxhelper.db`, `documents/`) contienen registros financieros, su NIF y facturas, por eso se guardan en su perfil de usuario:

- **Ubicación:** Windows `%LOCALAPPDATA%\TaxHelper`, macOS `~/Library/Application Support/TaxHelper`, Linux `~/.local/share/taxhelper`. Otra ubicación se indica con la variable de entorno `TAXHELPER_DATA`.
- Los datos solo salen de su ordenador si usted lo decide: las copias van a la carpeta elegida y los botones de validación y de presentación envían el fichero generado a la AEAT.

### Copias de seguridad

1. **Ajustes** → **Copia de seguridad** → la carpeta que prefiera, por ejemplo `D:\Backups\TaxHelper`.
2. La aplicación guarda allí `taxhelper-backup-AAAAMMDD-HHMM.zip` (base de datos y documentos) al arrancar y tras cambiar datos, como máximo cada 6 horas; tras un intento fallido, el siguiente espera al menos 15 minutos. Se conservan las 30 copias más recientes y la primera de cada mes.
3. Copia manual: «Hacer copia ahora» o el enlace «Descargar copia» al pie de la página. Un fallo de la copia automática se muestra en el panel.

**Restauración:** **Ajustes** → **Restaurar una copia de seguridad**: elija un archivo zip o una copia de la carpeta de copias. Primero se comprueba el zip y los datos actuales se guardan en un zip dentro de la carpeta `before-restore` de la carpeta de datos (se conservan los tres últimos); después se sustituyen la base de datos y los documentos.

## Idiomas de la interfaz

Español, inglés, ruso. El idioma se elige en la cabecera o en **Ajustes → Idioma** («automático» sigue el idioma del navegador, con el inglés como alternativa). El formato de fechas e importes depende del idioma: `1 234,56 €`, `€1,234.56`, `1.234,56 €`.

## Desarrollo

Instale los paquetes con `pip install -r requirements-dev.txt` y ejecute los tests con `pytest`; usan su propia carpeta de datos vacía. La aplicación es el paquete `taxhelper`: `calc.py` contiene las reglas fiscales, `aeat/` los ficheros de la AEAT y el envío, `web/` las vistas y formularios de Flask; todos los textos de la interfaz están en `locales/`. Se agradecen informes de errores y pull requests.

## Apoyar al autor

Si la aplicación le ahorra tiempo, puede darle las gracias con un pago voluntario: **[Apoyar al autor](https://buy.stripe.com/28E9ATgho9Z98Hqft51wY00)** (el mismo enlace está en el banner en la parte superior de cada página).

## Licencia

Copyright (C) 2026 Nikita Kotov. Autonomo Tax Helper es software libre bajo la **GNU Affero General Public License v3.0 o posterior** (véase [LICENSE](LICENSE)): puede usarlo, estudiarlo, modificarlo y compartirlo. Si distribuye una versión modificada, o permite que otros la usen como servicio de red, debe entregarles el código fuente con la misma licencia.

## Exención de responsabilidad

El software se ofrece **tal cual, sin garantía de ningún tipo** (véase [LICENSE](LICENSE)). No constituye asesoramiento fiscal ni jurídico, y el autor no responde de los errores en las cifras, los ficheros o las declaraciones elaborados con él. Usted es responsable de los datos que introduce y de lo que presenta: antes de presentar, compare las cifras finales con su gestor o con el prerrellenado de la AEAT.
