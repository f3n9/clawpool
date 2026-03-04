FROM python:3.12-slim
WORKDIR /app
COPY services_instance_manager /app/services_instance_manager
EXPOSE 8080
CMD ["python", "-m", "services_instance_manager.main"]
