FROM public.ecr.aws/lambda/python:3.12

# Install only the google API client into the image. Keep image minimal.
COPY requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Copy handler
COPY google_api_client.py ${LAMBDA_TASK_ROOT}/

# Set the CMD to the handler function in the file we added
CMD ["google_api_client.lambda_handler"]
