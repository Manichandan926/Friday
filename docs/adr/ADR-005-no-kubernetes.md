# ADR 005: No Kubernetes / Bare Metal IoT

## Status
Accepted

## Context
Deploying FRIDAY using Kubernetes, Docker Swarm, or other heavy distributed orchestrators is a common architectural pattern for scale. However, FRIDAY is designed as a *Personal AI Operating System* running on edge hardware (laptops, NUCs, Raspberry Pis). The overhead of `etcd`, `kubelet`, and container networking overlays destroys the performance of low-resource hardware.

## Decision
FRIDAY will not use Kubernetes or container orchestrators for its internal topology. It will run directly on bare metal (or single containers) using its own internal Actor System for distributed communication. Any multi-node clustering will be achieved via lightweight peer-to-peer protocols (e.g., NATS or raw MQTT) rather than heavy infra layers.

## Consequences
- **Pros:** Maximum hardware utilization, zero infra complexity, easy installation for end users, lower power consumption.
- **Cons:** We must handle our own node discovery, health checking, and distributed state replication (if clustered later).
