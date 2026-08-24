# Using VyManager SDN controller & web GUI

The primary source of truth for [VyManager](https://github.com/Community-VyProjects/VyManager) is the linked GitHub repo. This section provides a quick overview, and focuses on the specifics of use with the Mono Gateway Development kit.

---

## 1. VyOS: CLI-first, web GUI optional

VyOS is a widely deployed enterprise-grade network operating system used at-scale across multiple cloud environments, virtualised deployments, and on a wide variety of 'bare metal' commodity hardware. A Graphical User Interface (GUI) in many cases is neither desired, or required, as VyOS is entirely configurable from the Command Line Interface (CLI).

The VyOS API provides an alternative programmatic means to interact with one, or many VyOS instances at-scale, and enables the creation of a GUI, if desired. The addition of a new REST API in the (present) VyOS 1.5.x branch is credited with improving this further, resolving long-standing challenges in adequately exposing sufficient coverage of underlying VyOS capabilities.

---

## 2. VyManager: SDN controller & web GUI

The [VyManager](https://github.com/Community-VyProjects/VyManager) community project provides a centralised Software Defined Networking (SDN) controller which enables the configuration, deployment and monitoring of multiple VyOS routers, across many-sites, all via a modern web interface. This is achieved by hooking the VyOS REST and GRAPHQL APIs, alongside enabling managed SSH access to provide additional interactive functionality, where required.

### 2.1 VyManager architecture

VyManager exists as three distinct component containers:

- **Frontend –** A [node.js](https://en.wikipedia.org/wiki/Node.js) webapp written in [TypeScript](https://en.wikipedia.org/wiki/TypeScript), using Prisma [ORM](https://en.wikipedia.org/wiki/Object%E2%80%93relational_mapping)
- **Backend –** A [python 3](https://en.wikipedia.org/wiki/History_of_Python#Version_3) based [FastAPI](https://en.wikipedia.org/wiki/FastAPI) client
- **Database –** Vanilla [PostgreSQL](https://en.wikipedia.org/wiki/PostgreSQL) 16 [relational database management system](https://en.wikipedia.org/wiki/Relational_database_management_system "Relational database management system") (RDBMS)

A more complete view of the tech stack used, see: [VyManager](https://github.com/Community-VyProjects/VyManager#tech-stack) 

### 2.2 VyManager hosting

The [VyManager](https://github.com/Community-VyProjects/VyManager) project provides a [Quick Start](https://github.com/Community-VyProjects/VyManager#quick-start) guide targeting [x86](https://en.wikipedia.org/wiki/X86) hosted [Docker](https://en.wikipedia.org/wiki/Docker_(software)) using `docker compose`. Other containerised environments like [Podman](https://en.wikipedia.org/wiki/Podman), via `podman-compose`, provide more modern alternative, utilising the same build artifacts. Alternative aarch64 hosting is also viable, and presents the option to deploy directly onto the Mono Gateway Development Kit, if desired.

>**NOTE:** Pay close attention to the flagged [Security Considerations](https://github.com/Community-VyProjects/VyManager#security-considerations). VyManager is a privileged management interface, and should always be isolated and secured appropriately. The use of [rootless Podman configurations](https://developers.redhat.com/blog/2020/09/25/rootless-containers-with-podman-the-basics) should also be strongly preferred to rootful (run as root) container execution, e.g. with Docker.

**Two deployment options will be addressed here:**

1. Deployment via Podman onto generic [x86](https://en.wikipedia.org/wiki/X86) [COTS](https://en.wikipedia.org/wiki/Commercial_off-the-shelf) hardware

2. Deployment via VyOS CLI, with Podman, onto the [aarch64](https://en.wikipedia.org/wiki/AArch64) Mono Gateway Development Kit

---

## 3. Common Requirements

>**WARNING:** By default, the VyManager web GUI is globally accessible, on all ports, including any you define as 'WAN' interfaces. VyManager does have a 'trusted origins' concept, but this does not provide an effective security boundary. **Always ensure you configure [firewall](https://docs.vyos.io/en/rolling/configuration/firewall/index.html) policy appropriately, and that your wider configurational choices (e.g. a dedicated management interface and/or [VRF](https://docs.vyos.io/en/rolling/configuration/vrf/index.html)) to manage and mitigate the risk of unauthorised access.**

In order for VyManager to access a running VyOS instance, we must configure an appropriate API key secret, and enable API access.

Via the USB console or SSH: login to your running Mono GW VyOS instance and run:

```bash
openssl rand -hex 64 			# Generate random hex 64-char string
```

This 64-character string will be your API key. Make a secure record of it, e.g. in a password manager, as you will need it again shortly.

>**NOTE:** The 64-char length is arbitrary, and length ≥32 characters will be sufficient for most use-cases.

Now enter configuration mode, and enable API access:

```bash
# Enter configuration mode
configure

# Define the VyOS API key (replace <SECURE_API_KEY> with your generated API key)
set service https api keys id vymanager key '<SECURE_API_KEY>'
set service https api keys id vymanager key a1b2c3d4....		# Example

# Enable the REST API (VyOS 1.5+ only)
set service https api rest

# Enable GraphQL API (required for dashboard streaming)
set service https api graphql

# Set GraphQL authentication to use the API key defined above
set service https api graphql authentication type key

# Commit, save & exit configuration mode
commit;save;exit
```

>**NOTE:** As above, substitute the entirety of \<VARIABLE\> for your generated value, you should have no \< or \> characters in these files, when completed.

VyOS will now respond to REST and GraphQL API endpoints when supplied with our API key. 

Next steps will now depend on where VyManager itself will be hosted, see: §3.2 or §3.3

## 4. Installation
### 4.1 x86 hosting with `podman`

>**NOTE: (Recommended option)** Using VyManager as the developers intended.

This is a minor variation on the [Vymanager Quick Start](https://github.com/Community-VyProjects/VyManager#quick-start) guide, adapted.

1) Ensure you have enabled the VyOS API in §3.1 

2) Identify a suitable x86 host

3) Install `podman` & `podman-compose`

Depending on your host OS, the detailed instructions for will vary. A comprehensive set of guides are available in the documentation for [podman](https://podman.io/docs/installation) and the counterpart docs for [podman-compose](https://podman-desktop.io/docs/compose/setting-up-compose).

4) Create a Vymanager folder, and enter it.

```bash
mkdir vymanager
cd vymanager
```

5) Create a file named `docker-compose.yml`, and copy in the below contents, which you will need to then customised for your use in the next few steps:

```yml
services:
  postgres:
    image: postgres:16-alpine
    container_name: vymanager-postgres
    environment:
      POSTGRES_USER: vymanager
      POSTGRES_PASSWORD: <POSTGRES_PASSWORD>
      POSTGRES_DB: vymanager
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped
    networks:
      - vymanager-network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U vymanager -d vymanager"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s

  backend:
    image: ghcr.io/community-vyprojects/vymanager-backend:beta
    container_name: vymanager-backend
    ports:
      - "8000:8000"
    volumes:
      - ./certs:/usr/local/share/ca-certificates/custom:ro
    env_file:
      - .env
    restart: unless-stopped
    networks:
      - vymanager-network
    depends_on:
      postgres:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/docs"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  frontend:
    image: ghcr.io/community-vyprojects/vymanager-frontend:beta
    container_name: vymanager-frontend
    ports:
      - "3000:3000"
    env_file:
      - .env
    depends_on:
      backend:
        condition: service_healthy
      postgres:
        condition: service_healthy
    restart: unless-stopped
    networks:
      - vymanager-network

networks:
  vymanager-network:
    driver: bridge

volumes:
  postgres_data:
    driver: local
```

6) Create a separate `.env` file, and copy in the below config, which will be customised in the next step:

```yml

# HEX SECRETS
SSH_ENCRYPTION_KEY=<SSH_KEY>

DATABASE_URL=postgresql://vymanager:<POSTGRES_PASSWORD>@postgres:5432/vymanager
FRONTEND_URL=http://frontend:3000

# BASE64 SECRETS
BETTER_AUTH_SECRET=<SHARED_SECRET>

## WEB GUI URL
BETTER_AUTH_URL=http://<SERVER_IP>:3000
NEXT_PUBLIC_APP_URL=http://<SERVER_IP>:3000

## ALLOWED LOGIN ORIGINS - comma-separated list - omit for global accesibility
TRUSTED_ORIGINS=http://<SERVER_IP>:3000,http://localhost:3000

### DEFINED ENV VARS + BACKEND NETWORK - NO CHANGES REQUIRED
NODE_ENV=production
VYMANAGER_ENV=production
BACKEND_URL=http://backend:8000
```

Create and substitute `<SECRETS>` in the `.env` and `docker-compose.yml` with your own generated values. For this we will again make use of `openssl rand` to generate the secrets required.

```bash 
<SSH_KEY>					# Generate random hex 64-char string
<POSTGRES_PASSWORD> 		# Generate random hex 64-char string
<SHARED_SECRET>  			# Generate random base64 32 bit string

# Generate random hex 64-char string
openssl rand -hex 64

# Generate random base64 32 bit string
openssl rand -base64 32
```

>**NOTE:** As previously, substitute the entirety of \<VARIABLE\> for your generated value. You should have no "\<" or "\>" characters in these files once completed.

>**NOTE:** The generated <POSTGRES_PASSWORD> ***MUST*** must the same and substituted into **BOTH** the `docker-compose.yml` **AND** the `.env` files

10) Finally, define the `<SERVER_IP>` in the `.env` file at which you your Web GUI will be served

This will be the IP of the host machine identified in step #1.

>**NOTE:** VyManager has a 'TRUSTED_ORIGINS' concept, which if defined, will only allow GUI logins from a source IPs defined here in as a comma-separated list. This should ***NOT*** be relied upon as security control and does ***NOT*** prevent access to the login page.

11) Build and start the three defined containers via:

```bash
# NB: unlike 'docker compose', 'podman-compose' is hypenated 

podman-compose up -d				# Builds and deploys the defined containers
```

12) Access the VyManager web GUI at the defined in the `.env` file, and complete the initial on-boarding process to create a local admin account

```
http://<SERVER_IP>:3000 
```

13) The initial onboarding process will require creating a username + password. Ensure these are saved into a password manager in keeping with good practice.

14) Define a 'Site' e.g. "Home"

15) Create a new 'Instance' and define the following:

**Basic info:**
- Instance Name

**Connection:**
- Host IP
- API Key (as defined in §3.1)

**SSH / Monitoring:**
- SSH username

```

16) Edit the newly added 'Instance' via the '3 dot' menu on the right, next to the 'Offline / Online' status indicator.

Generate a SSH key, and copy the Vyos commands provided.

Via the USB console or SSH: login to your running VyOS instance and run the indicated commands :
```bash
# Enter configuration mode
configure

# Define the SSH public key type for user <USER>
set system login user <USER> authentication public-keys type ssh-ed25519

# Define the VyManager SSH public key
set system login user <USER> authentication public-keys vymanager key <VYMANAGER SSH PUB KEY HERE>

# Apply, save to persistent config, exit configuration mode
commit;save;exit

```

17) In the VyManager webUI, confirm you have applied the SSH config. If completed correctly, you will see a small green indicator and "SSH Key Configured". Save changes

18) Connect to your VyOS instance, and begin configuring VyOS as you might any other Router with a WebUI.

19) DONE!

>**NOTE:** Thanks to the VyManager SSH key we've just created, you can use the webUI to access a console webshell, manage containers and much more.


### 4.2 aarch64 hosting on-device

>**NOTE: (Use with caution).** This adapts VyManager for the singular purpose of serving an on-device web GUI. It will require additional effort to deploy and use securely, given the inherently privileged and exposed position of a router in any network topology. 

>**WARNING:** Unlike the x86 install guide there is no <SERVER_IP> variable, as this defaults to the loopback address (127.0.0.1) when installing directly on the VyOS instance. This is not secure by default as the VyManager web GUI is then globally accessible, on all ports, including any you define as 'WAN' interfaces. VyManager does have a 'trusted origins' concept, but this does NOT provide an effective security boundary. **Always configure [firewall](https://docs.vyos.io/en/rolling/configuration/firewall/index.html) policy appropriately first, before you connect WAN, and ensure that your wider configurational choices (e.g. a dedicated management interface and/or [VRF](https://docs.vyos.io/en/rolling/configuration/vrf/index.html)) manage and mitigate the risk of unauthorised access.**

**Differences from x86 method in §4.1:**
- Container images use the aarch64 architecture
- Container definition uses the VyOS CLI's declarative syntax, not a `docker-compose.yml` file.

This section provides a guide to deployment of VyManager, directly onto the Mono Gateway Developer Kit, for the sole purpose of administering a VyOS instance on this hardware.

1) Ensure you have enabled the VyOS API in §3.1 

2) Identify a suitable aarch64 host

>**NOTE:** Theses same container sources can be used for other aarch64 hosts, e.g. a Raspberry Pi4/5 in conjunction with the deployment method shown previously in §4.1.

3) (other aarch64 hosts only) Install `podman` & `podman-compose` 

4. Via the USB console or SSH: login to your running Mono GW VyOS instance and run:

```bash
# In operational mode (default context post-login)
mkdir -p /config/vymanager/db /config/vymanager/api
add container image postgres:15-alpine
add container image ghcr.io/mihakralj/vymanager-api:beta-arm64
add container image ghcr.io/mihakralj/vymanager-ui:beta-arm64
```

5. Copy the below into a text editor:

```bash
# Enter configuration mode
configure
set service https api rest
set service https api graphql
set service https api graphql authentication type key

# Using the VyOS API key created in §3.1
set service https api keys id vymanager key <SECURE_API_KEY>

# Configure the postgres db container
set container name vymanager-db image 'postgres:15-alpine'
set container name vymanager-db allow-host-networks
set container name vymanager-db port db source '5432'
set container name vymanager-db port db destination '5432'
set container name vymanager-db environment POSTGRES_USER value 'vymanager'
set container name vymanager-db environment POSTGRES_PASSWORD value '<POSTGRES_PASSWORD>'
set container name vymanager-db environment POSTGRES_DB value 'vymanager'
set container name vymanager-db volume db-data source '/config/vymanager/db'
set container name vymanager-db volume db-data destination '/var/lib/postgresql/data'

# Configure the vymanager-api container
set container name vymanager-api image 'ghcr.io/mihakralj/vymanager-api:beta-arm64'
set container name vymanager-api allow-host-networks
set container name vymanager-api port api-port source '8000'
set container name vymanager-api port api-port destination '8000'
set container name vymanager-api volume ssh-keys source '/home/vyos/.ssh'
set container name vymanager-api volume ssh-keys destination '/root/.ssh'
set container name vymanager-api environment DATABASE_URL value 'postgresql://vymanager:<POSTGRES_PASSWORD>@127.0.0.1:5432/vymanager'
set container name vymanager-api environment BETTER_AUTH_SECRET value '<SHARED_SECRET>'
set container name vymanager-api environment BETTER_AUTH_URL value 'http://127.0.0.1:8000'
set container name vymanager-api environment FRONTEND_URL value 'http://127.0.0.1:3000'
set container name vymanager-api environment SSH_ENCRYPTION_KEY value '<SSH_KEY>'

# Configure the vymanager-ui (frontend) container
set container name vymanager-ui image 'ghcr.io/mihakralj/vymanager-ui:beta-arm64'
set container name vymanager-ui allow-host-networks
set container name vymanager-ui port web source '3000'
set container name vymanager-ui port web destination '3000'
set container name vymanager-ui environment BACKEND_URL value 'http://127.0.0.1:8000'
set container name vymanager-ui environment BETTER_AUTH_SECRET value '<SHARED_SECRET>'
set container name vymanager-ui environment BETTER_AUTH_URL value 'http://127.0.0.1:3000'
set container name vymanager-ui environment DATABASE_URL value 'postgresql://vymanager:<POSTGRES_PASSWORD>@127.0.0.1:5432/vymanager'
set container name vymanager-ui environment FRONTEND_URL value 'http://127.0.0.1:3000'
set container name vymanager-ui environment TRUSTED_ORIGINS value 'http://127.0.0.1:3000,http://localhost:3000'
set container name vymanager-ui environment NODE_ENV value 'production'
set container name vymanager-ui environment VYMANAGER_ENV value 'production'
set container name vymanager-ui environment SSH_ENCRYPTION_KEY value '<SSH_KEY>'
```

6. Generate your required secrets:

This can be done on the VyOS instance in op mode, if desired.

```bash 
<SSH_KEY>					# Generate random hex 64-char string
<POSTGRES_PASSWORD> 		# Generate random hex 64-char string
<SHARED_SECRET>  			# Generate random base64 32 bit string

# Generate random hex 64-char string
openssl rand -hex 64

# Generate random base64 32 bit string
openssl rand -base64 32
```

>**NOTE:** As previously, substitute the entirety of \<VARIABLE\> for your generated value. You should have no "\<" or "\>" characters in these files once completed.

7. Substitute the secrets generated in step 6. into the config in your text editor from step 5.

8. Via the USB console or SSH: login to your running Mono GW VyOS instance:
```
# Paste your prepared configuration from your text editor into VyOS

# commit (apply to running config); save (to persistent config); exit (back to operational mode)
commit;save;exit
```

9. Establish Router IP - or define Interface IP via USB console

If you have an existing router with DHCP, the connected VyOS interface will be reachable via this IP within your existing network.

Alternatively, via the USB console, login to your running Mono GW VyOS instance and assign an IP to an interface, and connect to this directly to continue configuration.

```bash
# Example to configure a temporary IP address 10.1.99.99 to eth0
configure
set interfaces ethernet address 10.1.99.99/24

commit;save;exit
```

10. Connect to VyManager via the IP of your Mono GW.

```
http://<ROUTER_IP>:3000 
```

11) The initial onboarding process will require creating a username + password. Ensure these are saved into a password manager in keeping with good practice.

12) Define a 'Site' e.g. "Home"

13) Create a new 'Instance' and define the following:

**Basic info:**
- Instance Name

**Connection:**
- Host IP
- API Key (as defined in §3.1)

**SSH / Monitoring:**
- SSH username

```

16) Edit the newly added 'Instance' via the '3 dot' menu on the right, next to the 'Offline / Online' status indicator.

Generate a SSH key, and copy the Vyos commands provided.

Via the USB console or SSH: login to your running VyOS instance and run the indicated commands :
```bash
# Enter configuration mode
configure

# Define the SSH public key type for user <USER>
set system login user <USER> authentication public-keys type ssh-ed25519

# Define the VyManager SSH public key
set system login user <USER> authentication public-keys vymanager key <VYMANAGER SSH PUB KEY HERE>

# Apply, save to persistent config, exit configuration mode
commit;save;exit

```

14) In the VyManager webUI, confirm you have applied the SSH config. If completed correctly, you will see a small green indicator and "SSH Key Configured". Save changes

15) Connect to your VyOS instance, and begin configuring VyOS as you might any other Router with a WebUI.

16) DONE!

>**NOTE:** Thanks to the VyManager SSH key we've just created, you can use the webUI to access a console webshell, manage containers and much more.