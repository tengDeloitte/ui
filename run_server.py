from fastapi import FastAPI, Request, APIRouter, HTTPException
from fastapi.templating import Jinja2Templates
import subprocess
import uvicorn
import logging
import boto3
import time
from typing import List, Dict
from fastapi import HTTPException
import logging

logging.basicConfig(level=logging.INFO)
region = 'us-east-1'
ecs_client = boto3.client('ecs', region_name=region)
ec2_client = boto3.client('ec2', region_name=region)
cluster_name = "reference_server_cluster"


def list_services(cluster_name):
    response = ecs_client.list_services(cluster=cluster_name)
    return response['serviceArns']


def list_tasks(cluster_name, service_name):
    response = ecs_client.list_tasks(cluster=cluster_name, serviceName=service_name)
    return response['taskArns']


def describe_tasks(cluster_name, task_arns):
    response = ecs_client.describe_tasks(cluster=cluster_name, tasks=task_arns)
    return response['tasks']


def get_task_public_ip(cluster_name, task_arn):
    task_details = ecs_client.describe_tasks(cluster=cluster_name, tasks=[task_arn])
    eni_id = None
    for detail in task_details['tasks'][0]['attachments'][0]['details']:
        if detail['name'] == 'networkInterfaceId':
            eni_id = detail['value']
            break
    if eni_id:
        eni_description = ec2_client.describe_network_interfaces(NetworkInterfaceIds=[eni_id])
        public_ip = eni_description['NetworkInterfaces'][0]['Association']['PublicIp']
        return public_ip
    else:
        return None


def update_service_and_get_ip(cluster_name, service_name, desired_count):
    update_response = ecs_client.update_service(
        cluster=cluster_name,
        service=service_name,
        desiredCount=desired_count
    )
    print("Waiting for the new task to start...")
    time.sleep(20)
    tasks = ecs_client.list_tasks(cluster=cluster_name, serviceName=service_name)['taskArns']
    if not tasks:
        return "No new task found"
    for _ in range(10):
        task_desc = ecs_client.describe_tasks(cluster=cluster_name, tasks=[tasks[0]])
        if task_desc['tasks'][0]['lastStatus'] == 'RUNNING':
            break
        time.sleep(6)
    public_ip = get_task_public_ip(cluster_name, tasks[0])
    if public_ip:
        return f"http://{public_ip}:7860"
    else:
        return "No public IP assigned to the task"


app = FastAPI()
router = APIRouter()
templates = Jinja2Templates(directory="templates")
server_process = None


@router.get("/")
async def welcome_page(request: Request):
    return templates.TemplateResponse("welcome.html", {"request": request})


app.include_router(router)


@app.post("/run_script")
async def run_script():
    started_services = {}
    all_services_in_use = False
    service_name = None
    services = list_services(cluster_name)
    for service in services:
        tasks = list_tasks(cluster_name, service)
        if not tasks and not started_services:
            print(f"No tasks running under [{service.split('/')[-1]}] service, starting this service...")
            service_name = service.split('/')[-1]
            ip_addr = update_service_and_get_ip(cluster_name, service_name, 1)
            started_services[service_name] = ip_addr
            print(f"Started service {service_name} with IP address: {ip_addr}")
            started_services[service_name] = ip_addr
            break
    else:
        all_services_in_use = True
        task_details = describe_tasks(cluster_name, tasks)
        for task in task_details:
            print("All services are running, please check later...")
            print(f"Task ARN: {task['taskArn']}, Status: {task['lastStatus']}")

    if service_name:
        return {"url": started_services[service_name], "all_in_use": all_services_in_use}
    else:
        return {"url": None, "all_in_use": all_services_in_use}

    # return {"url": started_services.get(service_name), "all_in_user": all_services_in_use}


@app.post("/start_service/{service_name}")
async def start_service(service_name: str):
    try:
        response = ecs_client.update_service(
            cluster=cluster_name,
            service=service_name,
            desiredCount=1
        )
        return {"message": f"Service {service_name} is starting.", "response": response}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/stop_service/{service_name}")
async def stop_service(service_name: str):
    try:
        response = ecs_client.update_service(
            cluster=cluster_name,
            service=service_name,
            desiredCount=0
        )
        return {"message": f"Service {service_name} is stopping.", "response": response}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/get_status")
async def get_status():
    try:
        services = list_services(cluster_name)
        status_data = []

        for service in services:
            service_name = service.split('/')[-1]
            tasks = list_tasks(cluster_name, service_name)
            if not tasks:
                status_data.append({"service": service_name, "status": "No CDERGPT running"})
                continue

            task_details = describe_tasks(cluster_name, tasks)
            task_status_list = []
            for task in task_details:
                task_status = {
                    "taskArn": task['taskArn'],
                    "lastStatus": task['lastStatus'],
                    "ipAddress": get_task_public_ip(cluster_name, task['taskArn'])
                }
                task_status_list.append(task_status)

            status_data.append({"service": service_name, "status": task_status_list})

        return status_data
    except boto3.exceptions.Boto3Error as boto_error:
        logging.error(f"An AWS Boto3 error occurred: {boto_error}")
        raise HTTPException(status_code=500, detail="An AWS Boto3 error occurred.")
    except ValueError as value_error:
        logging.error(f"A value error occurred: {value_error}")
        raise HTTPException(status_code=500, detail="A value error occurred.")
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/stop_script")
async def stop_script():
    pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
