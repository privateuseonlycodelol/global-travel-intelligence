FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# psycopg[binary] and rasterio ship manylinux wheels, so no GDAL/build
# toolchain is needed in this image.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data_cache /tmp/duckdb_spill

EXPOSE 8000

CMD ["python", "mcp_server.py"]
