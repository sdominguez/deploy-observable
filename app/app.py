import os
import random
import time
import logging
from flask import Flask, jsonify, request, Response
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

VERSION = os.getenv('APP_VERSION', 'v1')
FAILURE_RATE = float(os.getenv('FAILURE_RATE', '0'))
SLOW_RATE = float(os.getenv('SLOW_RATE', '0'))
SLOW_SECONDS = float(os.getenv('SLOW_SECONDS', '0.7'))

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(message)s')
REQ = Counter('demo_http_requests_total', 'Solicitudes HTTP', ['version','method','route','status'])
LAT = Histogram('demo_http_request_duration_seconds', 'Duración HTTP en segundos', ['version','method','route'], buckets=(.01,.025,.05,.1,.2,.3,.5,.75,1,1.5,2,5))

@app.get('/health/live')
def live():
    return jsonify(status='alive', version=VERSION)

@app.get('/health/ready')
def ready():
    return jsonify(status='ready', version=VERSION)

@app.get('/metrics')
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

@app.route('/api/orders', methods=['GET'])
def orders():
    started = time.perf_counter()
    status = 200
    if random.random() < SLOW_RATE:
        time.sleep(SLOW_SECONDS)
    if random.random() < FAILURE_RATE:
        status = 500
    elapsed = time.perf_counter() - started
    REQ.labels(VERSION, request.method, '/api/orders', str(status)).inc()
    LAT.labels(VERSION, request.method, '/api/orders').observe(elapsed)
    app.logger.info('event=request version=%s route=/api/orders status=%s duration_ms=%.1f request_id=%s', VERSION, status, elapsed*1000, request.headers.get('X-Request-ID','none'))
    if status == 500:
        return jsonify(error='simulated_failure', version=VERSION), 500
    return jsonify(version=VERSION, orders=[{'id':1,'total':125.0}]), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, threaded=True)
