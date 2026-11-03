FROM python:3.12-slim
ENV TZ=Asia/Shanghai PYTHONUNBUFFERED=1
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY app.py special_days.py cycle.py cycle_collaboration.py cycle.js cycle.css settings_service.py settings_ui.js settings_ui.css template_page.html /app/
COPY *.png /app/
ENV PORT=8795 DATA_DIR=/data
EXPOSE 8795
CMD ["python", "app.py"]
