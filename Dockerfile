FROM python:3.9
COPY . /workplace
WORKDIR /workplace
RUN pip3 install -r requirements.txt
EXPOSE 8000 8001 8002 8003 8004
CMD ["uvicorn", "run_server:app", "--host", "0.0.0.0"]