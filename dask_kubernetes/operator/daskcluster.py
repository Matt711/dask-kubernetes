import asyncio

import kopf
import kubernetes


def build_scheduler_pod_spec(name, image):
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": f"{name}-scheduler",
            "labels": {
                "dask.org/cluster-name": name,
                "dask.org/component": "scheduler",
            },
        },
        "spec": {
            "containers": [
                {
                    "name": "scheduler",
                    "image": image,
                    "args": ["dask-scheduler"],
                    "ports": [
                        {
                            "name": "comm",
                            "containerPort": 8786,
                            "protocol": "TCP",
                        },
                        {
                            "name": "dashboard",
                            "containerPort": 8787,
                            "protocol": "TCP",
                        },
                    ],
                    "readinessProbe": {
                        "tcpSocket": {"port": "comm"},
                        "initialDelaySeconds": 5,
                        "periodSeconds": 10,
                    },
                    "livenessProbe": {
                        "tcpSocket": {"port": "comm"},
                        "initialDelaySeconds": 15,
                        "periodSeconds": 20,
                    },
                }
            ]
        },
    }


def build_scheduler_service_spec(name):
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": name,
            "labels": {
                "dask.org/cluster-name": name,
            },
        },
        "spec": {
            "selector": {
                "dask.org/cluster-name": name,
                "dask.org/component": "scheduler",
            },
            "ports": [
                {
                    "name": "comm",
                    "protocol": "TCP",
                    "port": 8786,
                    "targetPort": 8786,
                },
                {
                    "name": "dashboard",
                    "protocol": "TCP",
                    "port": 8787,
                    "targetPort": 8787,
                },
            ],
        },
    }


def build_worker_pod_spec(name, namespace, image, n):
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": f"{name}-worker-{n}",
            "labels": {
                "dask.org/cluster-name": name,
                "dask.org/component": "worker",
            },
        },
        "spec": {
            "containers": [
                {
                    "name": "scheduler",
                    "image": image,
                    "args": ["dask-worker", f"tcp://{name}.{namespace}:8786"],
                }
            ]
        },
    }


def build_worker_group_spec(name, image, workers):
    return {
        "apiVersion": "kubernetes.dask.org/v1",
        "kind": "DaskWorkerGroup",
        "metadata": {"name": f"{name}-worker-group"},
        "spec": {
            "image": image,
            "workers": {
                "replicas": workers["replicas"],
                "resources": workers["resources"],
                "env": workers["env"],
            },
        },
    }


async def wait_for_scheduler(cluster_name, namespace):
    api = kubernetes.client.CoreV1Api()
    watch = kubernetes.watch.Watch()
    for event in watch.stream(
        func=api.list_namespaced_pod,
        namespace=namespace,
        label_selector=f"dask.org/cluster-name={cluster_name},dask.org/component=scheduler",
        timeout_seconds=60,
    ):
        if event["object"].status.phase == "Running":
            watch.stop()
        await asyncio.sleep(0.1)


@kopf.on.create("daskcluster")
async def daskcluster_create(spec, name, namespace, logger, **kwargs):
    logger.info(
        f"A DaskCluster has been created called {name} in {namespace} with the following config: {spec}"
    )
    api = kubernetes.client.CoreV1Api()

    # TODO Check for existing scheduler pod
    data = build_scheduler_pod_spec(name, spec.get("image"))
    kopf.adopt(data)
    scheduler_pod = api.create_namespaced_pod(
        namespace=namespace,
        body=data,
    )
    # await wait_for_scheduler(name, namespace)
    logger.info(
        f"A scheduler pod has been created called {data['metadata']['name']} in {namespace} \
        with the following config: {data['spec']}"
    )

    # TODO Check for existing scheduler service
    data = build_scheduler_service_spec(name)
    kopf.adopt(data)
    scheduler_pod = api.create_namespaced_service(
        namespace=namespace,
        body=data,
    )
    logger.info(f"Scheduler service in {namespace} is created")

    data = build_worker_group_spec("default", spec.get("image"), spec.get("workers"))
    kopf.adopt(data)
    api = kubernetes.client.CustomObjectsApi()
    worker_pods = api.create_namespaced_custom_object(
        group="kubernetes.dask.org",
        version="v1",
        plural="daskworkergroups",
        namespace=namespace,
        body=data,
    )
    logger.info(f"Worker group  in {namespace} is created")


@kopf.on.create("daskworkergroup")
async def daskworkergroup_create(spec, name, namespace, logger, **kwargs):
    logger.info(
        f"A scheduler service has been created called {data['metadata']['name']} in {namespace} \
        with the following config: {data['spec']}"
    )
    api = kubernetes.client.CoreV1Api()

    scheduler = kubernetes.client.CustomObjectsApi().list_cluster_custom_object(
        group="kubernetes.dask.org", version="v1", plural="daskclusters"
    )
    scheduler_name = scheduler["items"][0]["metadata"]["name"]

    num_workers = spec["workers"]["replicas"]
    for i in range(1, num_workers + 1):
        data = build_worker_pod_spec(scheduler_name, namespace, spec.get("image"), i)
        kopf.adopt(data)
        worker_pod = api.create_namespaced_pod(
            namespace=namespace,
            body=data,
        )
    # await wait_for_scheduler(name, namespace)
    logger.info(f"{num_workers} Worker pods in created in {namespace}")