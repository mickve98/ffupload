#!/usr/bin/with-contenv bashio

export PRINTER_HOST="$(bashio::config 'printer_host')"
export PRINTER_SERIAL="$(bashio::config 'printer_serial')"
export PRINTER_CHECKCODE="$(bashio::config 'printer_checkcode')"
export MAX_UPLOAD_MB="$(bashio::config 'max_upload_mb')"
export DEFAULT_MATERIAL="$(bashio::config 'default_material')"
export DEFAULT_QUALITY="$(bashio::config 'default_quality')"
export DEFAULT_INFILL="$(bashio::config 'default_infill')"

for v in PRINTER_SERIAL PRINTER_CHECKCODE; do
  [ "$(eval echo \$$v)" = "null" ] && export $v=""
done

if bashio::var.has_value "$PRINTER_CHECKCODE"; then
  bashio::log.info "Printer ${PRINTER_HOST} — using the HTTP API"
else
  bashio::log.warning "No check code set — using the legacy TCP protocol"
fi

if command -v prusa-slicer >/dev/null 2>&1; then
  bashio::log.info "Slicing engine ready: $(prusa-slicer --help 2>&1 | head -n1)"
else
  bashio::log.error "prusa-slicer not found — only pre-sliced uploads will work"
fi

exec gunicorn \
  --bind 0.0.0.0:5005 \
  --workers 1 \
  --threads 8 \
  --timeout 2400 \
  --access-logfile - \
  app:app
