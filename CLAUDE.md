# CLAUDE.md

## Shared Deployment Infrastructure

- In every deployment change, Dockerfiles and Docker Compose files must not create, run, or embed PostgreSQL, Redis, or RabbitMQ services.
- Do not add service definitions, images, containers, initialization jobs, or persistent volumes for these dependencies.
- PostgreSQL, Redis, and RabbitMQ are centrally managed by `../docker-helm.dofe.ai`; application deployments must connect to those externally managed services through configuration.

## Git Workflow

- After every modification, commit the changes to git, writing the commit message in Chinese.
- Prefer small, atomic commits; describe what changed and why, in Chinese. Do not bundle unrelated changes into one commit.

