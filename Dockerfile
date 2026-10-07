FROM python:3.12-slim
ENV TZ=Asia/Shanghai PYTHONUNBUFFERED=1
WORKDIR /app
COPY app.py backup.py /app/
COPY *.png /app/
ENV PORT=8795 DATA_DIR=/data
EXPOSE 8795
CMD ["python", "app.py"]
