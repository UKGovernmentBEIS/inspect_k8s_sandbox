# Docker Images

Any Docker images required by your evals will need to be available to the cluster.

## Best practices

A Docker image built from source (i.e. a Dockerfile) may change over time. For
reproducibility, it is recommended to build, tag and push the image to a _registry_.

In your `values.yaml` file, you should specify a tag (i.e. version) for the image rather
than the implicit `latest`.

A registry contains _repositories_ for each image, each with multiple tags for different
versions.

It is best practice to configure the repositories to have tag immutability i.e. once a
tag (e.g. `1.0.0`) is pushed, it cannot not be overwritten.

## AWS

If using AWS infrastructure, you might choose ECR as your image registry.

To [authenticate with
ECR](https://docs.aws.amazon.com/AmazonECR/latest/userguide/registry_auth.html), you can
use the following command, replacing the `<aws_acc_id>` with your AWS account ID and
adjusting the region as necessary:

```sh
aws ecr get-login-password --region eu-west-2 | \
    docker login --username AWS --password-stdin \
    <aws_acc_id>.dkr.ecr.eu-west-2.amazonaws.com
```

To create a new repository in ECR:

```sh
aws ecr create-repository --repository-name <repository-name> \
    --image-scanning-configuration scanOnPush=true \
    --image-tag-mutability IMMUTABLE
```

To build your image and push it to ECR:

```sh
IMAGE=<aws_acc_id>.dkr.ecr.eu-west-2.amazonaws.com/<repository-name>:<version>
docker build -t $IMAGE .
docker push $IMAGE
```
