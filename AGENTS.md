# AGENTS.md

## Shared Deployment Infrastructure

- In every deployment change, Dockerfiles and Docker Compose files must not create, run, or embed PostgreSQL, Redis, or RabbitMQ services.
- Do not add service definitions, images, containers, initialization jobs, or persistent volumes for these dependencies.
- PostgreSQL, Redis, and RabbitMQ are centrally managed by `../docker-helm.dofe.ai`; application deployments must connect to those externally managed services through configuration.

## Git Workflow

- After every modification, commit the changes to git, writing the commit message in Chinese.
- Prefer small, atomic commits; describe what changed and why, in Chinese. Push each completed commit to the configured upstream branch before reporting completion. Do not bundle unrelated changes into one commit.

## Source Of Truth Contracts

- GEO retrieval and diagnostic grounding must use `knowledge.dofe.ai` as the only knowledge source and must never fall back to relational company fields or local vector stores.
- In production, `sso.ixicai.cn` is the only user identity source. GEORank stores only authorization projections such as roles, activation state, and quotas; local registration, passwords, identity editing, and user creation are forbidden.
