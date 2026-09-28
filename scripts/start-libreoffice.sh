#!/usr/bin/env bash
# Arranca LibreOffice en modo headless con el socket UNO habilitado.
# - Si ya está escuchando en el puerto, no hace nada.
# - Si no hay ninguna instancia corriendo, elimina el archivo .lock huérfano
#   (queda tras un cierre forzado y hace que LibreOffice se cierre sin avisar).

set -euo pipefail

PORT="${LIBREOFFICE_UNO_PORT:-2002}"
LOCK="$HOME/.config/libreoffice/4/.lock"
LOG="${LIBREOFFICE_LOG:-/tmp/soffice-$USER.log}"
TIMEOUT=20

escuchando() { ss -ltn | grep -q "127.0.0.1:$PORT "; }

if escuchando; then
    echo "LibreOffice ya está escuchando en 127.0.0.1:$PORT"
    exit 0
fi

if ! pgrep -u "$USER" -f soffice.bin > /dev/null && [ -f "$LOCK" ]; then
    echo "Eliminando lock huérfano: $LOCK"
    rm -f "$LOCK"
fi

nohup soffice --headless \
    --accept="socket,host=localhost,port=$PORT;urp;" \
    --norestore > "$LOG" 2>&1 &

for _ in $(seq "$TIMEOUT"); do
    if escuchando; then
        echo "LibreOffice listo en 127.0.0.1:$PORT (log: $LOG)"
        exit 0
    fi
    sleep 1
done

echo "LibreOffice no abrió el puerto $PORT en ${TIMEOUT}s. Revisa $LOG" >&2
exit 1
