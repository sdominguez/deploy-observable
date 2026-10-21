# Laboratorio reproducible: observabilidad y decisión Canary

## Objetivo
Aplicar una regresión introducida en una nueva versión de una API, utilizando métricas y logs, sin depender exclusivamente de health checks.

## Escenario

- `api-v1`: responde normalmente.
- `api-v2`: tiene **20 % de probabilidad de HTTP 500** y **40 % de probabilidad de retraso de 800 ms** por solicitud; ambos eventos son independientes.
- `nginx`: envía **aproximadamente 80 %** del tráfico a v1 y **20 %** a v2 mediante `split_clients` con un identificador por solicitud.
- `prometheus`: consulta `/metrics` de cada versión cada 5 s.
- `grafana`: muestra un dashboard precargado.

**Alcance:** solo métricas y logs de Docker. No se incluyen trazas distribuidas, Loki ni Alertmanager. Las alertas de Prometheus pueden consultarse en su interfaz, pero no envían notificaciones.

## 1. Requisitos

Docker Engine y Docker Compose plugin (`docker compose version`), conexión para descargar imágenes y puertos locales **8080, 9090 y 3000** disponibles. Python 3 para ejecutar el generador de carga en el equipo anfitrión (no se necesitan librerías externas). Ejecutar dentro de una VM Vagrant es posible; en ese caso, abrir o reenviar esos puertos al anfitrión y ejecutar `load.py` **dentro de la VM** si Nginx está publicado allí.

## 2. Estructura

```text
observabilidad_lab/
├── compose.yaml
├── load.py
├── app/
│   ├── app.py
│   ├── Dockerfile
│   └── requirements.txt
├── nginx/nginx.conf
├── prometheus/
│   ├── prometheus.yml
│   └── alerts.yml
└── grafana/
    ├── dashboards/observabilidad.json
    └── provisioning/
        ├── datasources/prometheus.yml
        └── dashboards/provider.yml
```

## 3. Inicio 

En la carpeta `observabilidad_lab`:

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=20 nginx api-v1 api-v2
```

Abrir `http://localhost:8080/api/orders` varias veces. Las respuestas incluyen `"version":"v1"` o `"version":"v2"`; algunas solicitudes v2 devuelven HTTP 500. Verificar health checks desde los contenedores:

```bash
docker compose exec api-v1 python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health/ready').read().decode())"
docker compose exec api-v2 python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health/ready').read().decode())"
```

**Observación:** ambas versiones pueden reportar `ready` incluso cuando v2 devuelve errores de negocio y respuestas lentas. Ese contraste es deliberado.

## 4. Generación de tráfico

Desde la misma máquina donde se ejecuta Docker:

```bash
python load.py --seconds 240 --rps 12
```

En Windows también puede usarse `py load.py --seconds 240 --rps 12`. El script envía solicitudes **secuenciales** a `localhost:8080` con pausa entre solicitudes; su tasa efectiva puede ser menor a 12 rps cuando aparecen respuestas lentas. No es una herramienta profesional de carga ni garantiza una tasa constante. Mantenerlo activo al menos 2–3 minutos para poblar ventanas de consulta y reglas de alerta.

**Preguntas:** ¿qué porcentaje aproximado de solicitudes se asigna a v2? ¿Por qué la tasa global de errores puede parecer moderada si v2 falla mucho?

## 5. Explorar Prometheus

Abrir `http://localhost:9090/targets`. Ambos targets `api-v1:8000` y `api-v2:8000` deben aparecer `UP`. Luego usar `http://localhost:9090/graph`.

**Consultas (copiar una por vez):**

```promql
sum by (version) (rate(demo_http_requests_total[1m]))
```

```promql
100 * sum by (version) (rate(demo_http_requests_total{status=~"5.."}[1m])) / clamp_min(sum by (version) (rate(demo_http_requests_total[1m])), 0.001)
```

```promql
histogram_quantile(0.95, sum by (le, version) (rate(demo_http_request_duration_seconds_bucket[1m])))
```

```promql
up{job="demo-api"}
```

**Interpretación esperada:** v2 presenta mayor porcentaje de 5xx y p95 superior a v1. No esperar valores exactos: el generador, la selección del backend y los fallos son probabilísticos. La latencia está en **segundos** en la métrica.

## 6. Explorar Grafana

Abrir `http://localhost:3000`. Usuario `admin`, contraseña `admin` **solo para el laboratorio local**. Ir a **Dashboards → Despliegue → Canary · Observabilidad de API**. El origen de datos Prometheus y el dashboard se cargan automáticamente.

Paneles: solicitudes/s por versión, porcentaje de 5xx por versión y latencia p95 por versión. Comparar el comportamiento de v1 y v2; identificar cuándo se introdujo tráfico y cómo cambian las series. Si aparece `No data`, esperar varios scrapes y comprobar que `load.py` esté ejecutándose.

## 7. Consultar logs y alertas

```bash
docker compose logs --since=5m api-v2
docker compose logs --since=5m api-v1
```

Los registros incluyen `version`, `route`, `status`, `duration_ms` y `request_id` recibido desde Nginx. El formato es de pares `clave=valor` para facilitar lectura; no es JSON estricto. Los logs de este ejemplo no explican una causa real de base de datos: la falla es **simulada**.

En `http://localhost:9090/alerts` revisar `CanaryHighErrorRate` y `CanaryHighP95Latency`. Deben evaluarse con tráfico sostenido; pueden tardar más de un minuto en pasar de *pending* a *firing*. Si el tráfico se interrumpe, las condiciones dejan de sostenerse.

## 8. Decisión y rollback manual

**Criterios didácticos definidos antes del experimento:** no promover v2 si supera **8 % de HTTP 5xx** o **500 ms de p95** sostenidos en la ventana observada. Comparar con v1 y confirmar que hay tráfico suficiente.

En `nginx/nginx.conf`, sustituir:

```nginx
split_clients "${request_id}" $target_upstream {
  80% api_v1;
  *   api_v2;
}
```

por:

```nginx
split_clients "${request_id}" $target_upstream {
  100% api_v1;
}
```

Recrear Nginx para cargar el nuevo archivo:

```bash
docker compose up -d --force-recreate nginx
```

Continuar el tráfico 1–2 minutos. Confirmar que ya no aumentan los contadores de v2 y que la tasa de errores **global** se recupera. Las series históricas de v2 seguirán visibles hasta salir de la ventana temporal: no significa que todavía reciba tráfico. El `up` de v2 puede seguir siendo 1 porque Prometheus continúa consultando `/metrics` aunque Nginx ya no envíe solicitudes de usuarios.

**Importante:** esto es un **rollback del enrutamiento**, no un rollback de imagen o de base de datos. No ejecutar `docker compose down -v` como mecanismo de rollback.

## 9. Extensiones opcionales

1. Cambiar `FAILURE_RATE` y `SLOW_RATE` de v2 y recrear ese servicio (`docker compose up -d --force-recreate api-v2`); comparar sensibilidad de los indicadores.
2. Cambiar Canary de 20 % a 5 % y discutir la inestabilidad estadística por bajo volumen.
3. Incorporar un endpoint que haga una llamada HTTP a otro servicio, instrumentarlo con OpenTelemetry y exportar trazas a Jaeger. Esta extensión **no** está implementada en el proyecto base.
4. Proponer un pipeline de GitHub Actions con compuerta manual de promoción, utilizando la evidencia del dashboard.

## 10. Entregables

- Captura de `docker compose ps` y targets `UP`.
- Tres consultas PromQL y explicación de sus resultados.
- Captura del dashboard con ambas versiones.
- Tres registros representativos de v2, incluyendo al menos un error 500.
- Decisión de promover/mantener/revertir v2, justificada con indicadores.
- Evidencia de la modificación de enrutamiento y efecto observado después del rollback.
- Reflexión: por qué un health check `ready` no garantiza que un Canary deba promoverse.

## 11. Resolución de problemas

- **Puerto ocupado:** identificar el proceso o cambiar el mapeo del puerto correspondiente en `compose.yaml` y ajustar las URLs.
- **Targets DOWN:** `docker compose ps`, `docker compose logs prometheus api-v1 api-v2`, verificar que los contenedores compartan red Compose.
- **No data:** generar tráfico y esperar al menos dos scrapes; los histogramas se crean cuando llega la primera solicitud a `/api/orders`.
- **Grafana no carga panel:** revisar `docker compose logs grafana`, origen de datos Prometheus y dashboard provisionado.
- **Nginx falla al reiniciar:** validar sintaxis con `docker compose run --rm --no-deps nginx nginx -t` o revisar `docker compose logs nginx`; restaurar el archivo original.
- **Windows + Vagrant:** si el navegador del anfitrión no llega a la VM, configurar forwarded ports para 8080, 9090 y 3000 en el Vagrantfile; evitar conflictos con otros servicios.

## 12. Limpieza y seguridad

```bash
docker compose down
```

La pila es **solo para desarrollo local**: Grafana usa credenciales triviales, Prometheus/Grafana no tienen TLS y los puertos están publicados. No exponerla a Internet ni utilizar estas credenciales en producción. `docker compose down` elimina los contenedores/red de este proyecto; las métricas no se conservarán de forma persistente al recrear Prometheus, porque no se configuró volumen de datos.
