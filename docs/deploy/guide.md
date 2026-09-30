# Putting 2nd Mind on AWS, and running it

A first-time, end-to-end guide. By the end you will have `https://2nd-mind.<domain>` running on one
small AWS server behind the access code, deployed by GitHub Actions, and you will have done every
operation you'd ever need: deploy, roll back, flip the kill switch, restore a backup, read the logs,
patch, stop and start, and read the bill. Then Part 4 shows how to leave AWS before the free credit
runs out.

Everything in it was written so that you follow it in order and tick each box. It is written for
someone who has read about AWS but never used it: no step assumes you know what a VPC is (the
glossary at the end says, in a sentence).

**How to use it.** Each step has the same shape:

- **Where**: the console page (reach it with the search bar at the top of the AWS console, because
  the menus move around and the search doesn't), or the command to run.
- **Do**: what to click or type.
- **Check**: what you should see, and a command that proves it, because a console screenshot can
  change but a command doesn't.
- **Why**: one or two lines on what this piece is for.
- **If it fails**: the likely causes. The troubleshooting section near the end has the rest.

A step that starts drawing credit says so, with how much a month. Commands run from the repository
root on your laptop unless a step says otherwise. Placeholders are in angle brackets: `<domain>` is
the domain you buy, `<account-id>` your 12-digit AWS account number, `<owner>` your GitHub user name.

**Stuck?** Paste the error to the session. The fix goes into this guide or the code, and the sprint
report lists every fix.

**Time.** Part 1 takes an afternoon, most of it waiting (a domain to delegate, an email to arrive).
Part 2 is about two hours. Part 3 is a set of exercises to do over a few days.

**What the session did and what you do.** The session wrote the app, the Terraform, the host
scripts, the deploy workflow and this guide, and tested them without an AWS account. It never held
AWS credentials. You do everything that touches AWS or your accounts: sign-up, the domain, keys,
`terraform apply`, secrets, GitHub settings and the first deploy.

## Facts this guide relies on, and when they were checked

Checked on **30 September 2026** against AWS's own pages. Prices and offers change: if a page here
disagrees with what you see, stop and trust the page.

- A new AWS account on the **Free plan** gets **$100 of credit at sign-up**, and can earn **up to $100
  more** by completing activities in the console's *Explore AWS* widget: **$20 for each of five**
  (launch an EC2 instance, set up a Budget, launch an RDS database, build a Lambda function, try a
  prompt in the Bedrock playground). Credits usually appear within about 10 minutes of finishing
  an activity, and **expire 12 months after the account is created**.
  [Free Tier FAQs](https://aws.amazon.com/free/free-tier-faqs/),
  [the five activities](https://builder.aws.com/content/2zmBcwokU8Y0C1zacGmXsRDpXP6/aws-free-tier-unlock-dollar200-in-free-credits-for-new-users)
- The Free plan **ends at whichever comes first: 6 months after you opened the account, or when the
  credits run out.** When it ends AWS **closes the account** (and keeps your data for 90 days, during
  which you can upgrade to the Paid plan). A Free-plan account cannot be billed.
  [when it starts and ends](https://repost.aws/knowledge-center/aws-free-tier-account-start-expire)
- On accounts opened since July 2025, EC2 instance types `t3.micro`, `t3.small`, `t4g.micro`,
  **`t4g.small`**, `c7i-flex.large` and `m7i-flex.large` are eligible on the Free plan. (AWS has also
  run a `t4g.small` free-trial offer through 31 December 2026: a bonus if it is still there, never
  something the plan depends on.)
- Oracle Cloud's Always Free tier (Part 4): 2 ARM cores, 12 GB of memory and 200 GB of disk, cut
  from a larger allowance on 15 June 2026, and a quiet instance is reclaimed after 7 idle days unless
  the account is upgraded to Pay As You Go.
  [resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm),
  [FAQ](https://www.oracle.com/cloud/free/faq/),
  [the June 2026 cut](https://www.infoq.com/news/2026/07/oracle-cloud-free-tier-limits/)

**Accounts with an Indian address** are run by AISPL, a local AWS entity, and the session could not
confirm the credit offer there. Step 2 has you check before anything is built.

---

# Part 1: before any infrastructure

Nothing here needs the session's Terraform. You can do all of it first.

## Step 1. What you'll build, and what it costs

- [ ] I've read this step.

```
DNS: Cloudflare free plan, DNS only (no proxy): 2nd-mind.<domain>  A  <Elastic IP>
browser ─https─▶ Elastic IP ─▶ EC2 t4g.small (Ubuntu 24.04 arm64, one AZ, 80/443 only, no SSH)
                                 Caddy edge: TLS from Let's Encrypt, routes by hostname
                                   2nd-mind.<domain> ─▶ web (nginx: SPA + /v1 proxy) ─▶ api
                                 worker · Postgres 16 + pgvector · Redis   (compose, same file
                                 as make up-prodlike; data on its own encrypted volume)
host role: read SSM /secondmind/prod/*, write backups to S3, send logs
CI ─▶ push arm64 images to GHCR (public) ─▶ GitHub OIDC ─▶ SSM send-command: pull, migrate, up,
      wait for /readyz
backups: daily volume snapshots (7 kept) + nightly pg_dump to S3 (30 days)
tracing: Langfuse Cloud, metadata only
money: new-account credits ($200); Budgets at $1 net and $25 gross a month
```

**What draws credit, a month** (on-demand prices for `us-east-1`; rupees at about ₹89 to the dollar):

| Piece | Dollars | Rupees |
|---|---:|---:|
| EC2 `t4g.small`, always on | about 12.3 | about 1,090 |
| The public IPv4 address (Elastic IP) | 3.65 | about 325 |
| Root disk 12 GB + data disk 10 GB (gp3) | about 1.8 | about 160 |
| Daily snapshots of the data disk (7 kept) | about 0.5 | about 45 |
| The backup bucket (S3), logs (CloudWatch), parameters (SSM), Budgets, Session Manager | cents | cents |
| **Total** | **about 18** | **about 1,600** |

Model calls are not on this list: they are paid from the $16 of provider credit you already have,
capped by the app itself.

**How the credit works.**

- $100 arrives at sign-up and $100 more when you complete the five activities (steps 2 and 3 do all
  five). That is $200, and it lasts about 11 months at $18 a month: less if you stop the host, more
  if you're careful.
- The Free plan **can't bill you**, and closes the account at 6 months. So the plan is to learn on
  AWS for a few months and then **move the app to a free host** (Part 4), or **upgrade to the Paid
  plan**, which keeps the leftover credit until month 12 and bills normally after that.
- You'll put the dates in the checklist at the end of step 2 and set a calendar reminder.

**How to stop the draw.**

- `make host-stop` stops the instance (before launch nobody visits): it stops the $12.3, and the
  address and disks (about $5.60 together) keep drawing. `make host-start` brings it back.
- Tearing everything down (Part 4) stops all of it.

## Step 2. The account, safely

### 2a. Sign up on the Free plan

- [ ] Done: a new AWS account on the **Free plan**.

**Where:** https://aws.amazon.com/free, "Create a Free Account".

**Do:** use an email address you'll keep (a new account means a new address for the root user).
Choose the **Free plan** (not Paid) when asked. Give a card for identity (the Free plan can't
charge it). Pick the **Basic support** plan.

**Check:** you can sign in to the console. In the search bar type **Billing and Cost Management**
and open it.

**Why:** the Free plan is what makes "₹0 in cash" true: it can't bill you.

**If it fails:** an Indian address means an AISPL account, whose offer wasn't confirmed: see 2b.

### 2b. The credit check, first

- [ ] **Billing and Cost Management → Credits** shows **$100** of credit.

**Where:** search bar → **Billing and Cost Management** → **Credits** in the left menu.

**Check:** a line of roughly $100 (the "Free Tier" credit), with an expiry 12 months away. There's no
free command for this one (the Cost Explorer API costs a cent a call): the page is the check.

**Why:** everything after this assumes the credit is there.

**If it isn't there: stop here and tell the session.** Don't build anything. The fallback is decided
then: Oracle's free tier from the start (only the Terraform changes: the host script is plain
Linux), or paying about ₹1,600 a month.

### 2c. Lock the root user, then put it away

- [ ] MFA is on for the root user.
- [ ] The root password is in a password manager, and you don't use it again for daily work.

**Where:** click your account name (top right) → **Security credentials**.

**Do:** under **Multi-factor authentication (MFA)** → **Assign MFA device** → *Authenticator app*.
Scan the QR code with an authenticator (a second device is safest) and enter two consecutive codes.
Do **not** create access keys for the root user.

**Check:** the same page lists the MFA device. Signing out and in asks for a code.

**Why:** the root user can do anything, including close the account. It is only for billing and
account tasks; everything else uses the admin user below.

### 2d. An admin user in IAM Identity Center, with MFA

- [ ] Done: I sign in to the AWS access portal as an admin user with MFA.

**Where:** the region selector (top right) → **US East (N. Virginia) us-east-1**. Then search bar →
**IAM Identity Center**.

**Do:**

1. **Enable** IAM Identity Center (choose "with AWS Organizations": it's free, and it creates an
   organisation with your account as its only member).
2. **Users → Add user**: a user name, your email address, your name. Skip groups.
3. **Permission sets → Create permission set → Predefined → AdministratorAccess**, session duration
   8 hours.
4. **AWS accounts →** tick your account **→ Assign users or groups →** your user **→** the
   AdministratorAccess permission set **→ Submit**.
5. Open the invitation email, accept it, set a password, and register an MFA device when asked.
6. On the Identity Center **Dashboard**, note the **AWS access portal URL** (it looks like
   `https://d-xxxxxxxxxx.awsapps.com/start`).

**Check:** open the access portal URL, sign in, and see your account with an **AdministratorAccess**
link. (The command version is in step 4.)

**Why:** you get short-lived sessions instead of permanent keys, which is how AWS wants people to
sign in, and the CLI in step 4 uses the same login.

**If it fails:** Identity Center is regional and its home region can't be moved: if you enabled it in
another region, disable it and start again in us-east-1. If the console says Identity Center isn't
available to your account, tell the session before working around it: the workaround (an IAM user
with an access key) puts a permanent key on your laptop.

### 2e. The region

- [ ] The console's region selector says **N. Virginia (us-east-1)**.

**Why us-east-1:** every turn calls model providers hosted in the US, so the server sits next to
them (about 200 ms saved on each call, and the newest AWS features arrive there first). Everything in
this guide assumes it.

### 2f. A zero-spend budget (also the first credit task)

- [ ] A **zero spend budget** exists, and its email is right.

**Where:** search bar → **Budgets** (under Billing and Cost Management) → **Create budget**.

**Do:** choose **Use a template (simplified)** → **Zero spend budget** → enter your email → **Create
budget**.

**Check:**

```sh
aws budgets describe-budgets --account-id <account-id> --profile secondmind --query 'Budgets[].BudgetName'
```

(The profile comes from step 4: come back to this command after it.) Within about 10 minutes the
**Explore AWS** widget on the console home page should show this activity done, and **Credits**
should gain $20.

**Why:** it emails you at the first cent of charge. Terraform adds two more Budgets later
(at $1 of net cost and $25 of gross cost), and this one stays as a third alarm.

### 2g. The month-5 reminder

- [ ] My calendar has a reminder at month 5.

Fill this in with today's date:

```
Account opened:            ____ / ____ / ________
Free plan ends (+6 months): ____ / ____ / ________
Move or upgrade by (+5 months): ____ / ____ / ________
Credits expire (+12 months):    ____ / ____ / ________
```

Set a calendar event on the "move or upgrade by" date, with the title *2nd Mind: leave AWS or upgrade
(guide Part 4)*. **Why:** at 6 months AWS closes a Free-plan account. Part 4 covers the two choices.

## Step 3. The other four credit tasks

Each takes minutes, and each is deleted right after so it stops drawing credit. After each, wait
about 10 minutes and check that **Billing and Cost Management → Credits** grew by $20 (total $100
from sign-up plus $20 for each finished task; the zero-spend budget was the first).

Do them from the **Explore AWS** widget on the console home page if you like (it walks you through
each), or by the steps below.

### 3a. The console lab: launch an instance by clicking, connect, terminate

- [ ] Done, and the instance is **terminated**.

This one teaches the console before the real apply, so it takes a little longer (about 15 minutes).
It draws a few cents while it exists.

**Where and do:**

1. Search bar → **IAM** → **Roles → Create role**. Trusted entity: **AWS service**, use case
   **EC2**. Attach the policy **AmazonSSMManagedInstanceCore**. Name it `lab-ssm`. (This is what
   lets Session Manager reach an instance: the same idea as the real host's role.)
2. Search bar → **EC2** → **Launch instance**. Name `lab`. Image **Ubuntu Server 24.04 LTS**
   (64-bit Arm). Type **t4g.small**. **Key pair: Proceed without a key pair.** Network: leave the
   default, **Create security group**, remove any inbound rule (SSH included). Under **Advanced
   details → IAM instance profile** choose `lab-ssm`. **Launch instance.**
3. Wait for **Instance state: Running**, then another 3 to 5 minutes. Select it → **Connect →
   Session Manager → Connect**. (If Connect is greyed out, the agent hasn't registered yet.)
4. In the terminal type `whoami; uname -m; free -h`.
5. Back at the instance list: **Instance state → Terminate instance.**

**Check:** in the terminal you saw `aarch64` and about 2 GB of memory. Afterwards:

```sh
aws ec2 describe-instances --profile secondmind --region us-east-1 \
  --filters Name=tag:Name,Values=lab --query 'Reservations[].Instances[].State.Name'
```

shows `"terminated"`.

**Why:** you have now done by hand what Terraform does in step 8, including a role, a security group
and Session Manager instead of SSH.

**If it fails:** if the console refuses `t4g.small` on the Free plan, tell the session (the plan's
eligible list changes); `t3.small` is the alternative for the lab only. Also delete the `lab-ssm`
role and the lab security group when you're done (IAM → Roles; EC2 → Security Groups).

### 3b. An RDS database, created and deleted

- [ ] Done, and the database is **deleted**.

**Where and do:** search bar → **RDS** → **Create database** → **Easy create** → **Free tier**
template (choose the smallest offered: a PostgreSQL `db.t4g.micro`) → a DB instance identifier
`lab`, master password of your choosing → **Create database**. When its status is **Available**
(5 to 10 minutes) open it and look around (Configuration, Connectivity). Then **Actions → Delete**:
untick *Create final snapshot* and *Retain automated backups*, tick the acknowledgement, and type
`delete me`.

**Check:** `aws rds describe-db-instances --profile secondmind --region us-east-1 --query 'DBInstances[].DBInstanceIdentifier'`
returns an empty list once the deletion finishes.

**Why:** it's an activity that earns $20. The real app runs Postgres in a container on the host, not
here, because RDS is one more paid piece that wouldn't move to a free host.

### 3c. A Lambda function, run once and deleted

- [ ] Done, and the function is **deleted**.

**Where and do:** search bar → **Lambda** → **Create function** → **Author from scratch** → name
`lab`, runtime **Python 3.x**, architecture arm64 → **Create function**. Under **Configuration →
Function URL** create one (auth type **NONE** is fine for a throwaway, since the function only says
hello). Open the URL in a browser, then **Test** the function once. Then **Actions → Delete function**.

**Check:** `aws lambda list-functions --profile secondmind --region us-east-1 --query 'Functions[].FunctionName'`
is empty.

**Why:** an activity that earns $20, and a look at another way AWS runs code.

### 3d. One prompt in the Bedrock playground

- [ ] Done: one prompt sent.

**Where and do:** search bar → **Amazon Bedrock** → **Playgrounds → Chat / Text**. **Select model**
and choose an **Amazon Nova** model (Amazon's own: cheap, and no separate subscription). Send one
short prompt.

**Check:** you got a reply. There is nothing to delete.

**Why:** an activity that earns $20. Choose an Amazon model, not a partner model: a partner model
can need a marketplace subscription, which is a separate bill.

**If it fails:** if it asks you to request access to a model, request the Amazon one; if it only
offers partner models, tell the session.

### The credit check for this step

- [ ] **Credits** now shows about **$200** in total (or $20 more for each task done so far).

If a task's $20 hasn't shown up after an hour, see *A credit task's $20 didn't show up* in the
troubleshooting section.

## Step 4. Your laptop

- [ ] `aws sts get-caller-identity --profile secondmind` prints your account.

**Where:** a terminal.

**Do:**

```sh
brew install awscli          # AWS CLI v2
brew install gh              # the GitHub CLI, for `make deploy`
brew install --cask session-manager-plugin   # for `aws ssm start-session`
aws configure sso
```

`aws configure sso` asks for: a session name (say `secondmind`), the **SSO start URL** (the access
portal URL from step 2d), the **SSO region** (`us-east-1`), and scopes (accept the default). A
browser opens to approve it. Pick your account and the **AdministratorAccess** role. Give the profile
the name **`secondmind`** (the Makefile uses it by default), region `us-east-1`, output `json`. Then:

```sh
aws sso login --profile secondmind
export AWS_PROFILE=secondmind      # or put it in your shell profile
gh auth login                      # GitHub CLI: choose HTTPS and log in through the browser
```

**Check:**

```sh
aws sts get-caller-identity --profile secondmind
```

prints your `Account` (12 digits) and an `Arn` containing `AWSReservedSSO_AdministratorAccess`.

Terraform, tflint and trivy run in containers, so there's nothing else to install; you do need
Docker running (Docker Desktop or Colima). Confirm the session's offline checks pass on your
machine, with no AWS involved:

```sh
make tf-check
```

**Why:** the same SSO login serves the CLI and the Terraform container, and expires on its own.
**If it fails:** `aws sso login` again if a command says the token expired (it does, every few
hours). If Docker isn't running, `make tf-check` says so.

## Step 5. The domain and DNS

You need a domain, and DNS on Cloudflare's free plan. The domain is the only cash you'll spend
(about ₹150 to ₹1,000 a year, depending on the name and ending), and it doesn't depend on AWS: when
you move hosts, one DNS record changes.

### 5a. Choose a name and buy it

- [ ] I own `<domain>`.

Buy from **any registrar, not Route 53** (Route 53 would tie DNS to AWS). What to compare is the
**renewal** price, not the first-year sale price, whether WHOIS privacy is included, and whether it
takes a payment method you have (UPI or an Indian card, in many cases). As a rough guide, checked at
the registrars' own pricing pages (prices move: read the checkout):

| Registrar | Typically | Watch for |
|---|---|---|
| Cloudflare Registrar | sells at wholesale cost, no markup; renews at the same price | only for domains whose DNS is on Cloudflare (which we want); fewer endings than others |
| Porkbun | low first year and modest renewal; free WHOIS privacy | check the renewal for your ending |
| Namecheap | low first year, higher renewal on some endings | the renewal price after year one |
| A local registrar (GoDaddy, BigRock and others) | can accept UPI | steep renewals and upsells: read the total |

A short, readable name works (`yourname.dev`, `yourname.me`). Recruiters will type it.

### 5b. Put DNS on Cloudflare's free plan

- [ ] `dig NS <domain> +short` shows two Cloudflare name servers.

**Where:** https://dash.cloudflare.com → **Add a domain**.

**Do:** enter `<domain>`, choose the **Free** plan, let it scan (nothing to import), and it gives you
two name servers (like `ada.ns.cloudflare.com`). At your registrar, replace the domain's name
servers with those two. (Bought at Cloudflare Registrar? It's already done.)

**Check:**

```sh
dig NS <domain> +short
```

shows the two Cloudflare servers. This can take from minutes to a day. Cloudflare's dashboard also
shows **Active** when it has seen the change.

**Why:** Cloudflare's free DNS costs nothing, and the domain then has nothing to do with AWS.
**If it fails:** if `dig` still shows the registrar's servers after a few hours, re-check that the
change was saved at the registrar (some have a separate "custom DNS" toggle).

### 5c. The free alternative, if you'd rather spend nothing

- [ ] (Optional) I use DuckDNS instead of a bought domain.

At https://www.duckdns.org, sign in and create a subdomain such as `yourname`: your address is then
`yourname.duckdns.org`. It works with this guide by setting the domain to `duckdns.org` and the app
subdomain to your name (the guide's `2nd-mind.<domain>` becomes `yourname.duckdns.org`; see
`terraform.tfvars` in step 8 and note the `app_subdomain` variable). Point it at the Elastic IP in
step 8 through DuckDNS's page instead of Cloudflare. It looks less finished, and everything else is
identical.

## Step 6. The outside accounts

### 6a. Langfuse Cloud (tracing)

- [ ] I have a Langfuse project, its public key, secret key and project id.

**Where:** https://us.cloud.langfuse.com → sign up.

**Do:** create an organisation and a project `secondmind` on the **Hobby (free)** plan, **US
region**. Under the project's **Settings → API Keys** create a key pair. Note the **public key**
(`pk-lf-…`), the **secret key** (`sk-lf-…`, shown once) and the **project id** (in the project's
URL and under Settings). Under **Settings → Data retention**, if it offers a choice, keep the shortest
(the Hobby plan keeps data for 30 days).

**Why:** traces show every step of a turn, with timing and cost, and **metadata only**: the app never
sends message text (`TRACE_INCLUDE_CONTENT=false`).

### 6b. Production keys at Anthropic and OpenAI, with their own spend limits

- [ ] New production keys exist, each with a provider-side spend limit.

**Do:** make **new** keys for production (not your laptop's), so either can be revoked on its own:

- Anthropic Console → **API keys → Create key** named `secondmind-prod`. Then **Settings → Limits**:
  set a **monthly spend limit** of about $5.
- OpenAI Platform → **Projects → Create project** `secondmind-prod`, then create an API key inside it.
  Under the project's **Limits**, set a **monthly budget** of about $3.

Keep the keys in your password manager for step 10. **Why:** the app has its own caps (`$0.50` a day,
`$5` a month, and a lifetime share per provider), and the provider's limit is a second guard that
still works if the app is misconfigured.

### 6c. GitHub: the environment, the variables, and the packages

- [ ] The `production` environment exists with its branch rule.
- [ ] The repository variables are set (some wait until later steps: the table says which).
- [ ] (After the first CI run, in step 11) both packages are public.

**Where:** your repository on GitHub → **Settings**.

**Do:**

1. **Environments → New environment**, name it exactly **`production`**. Under **Deployment branches
   and tags** choose **Selected branches and tags** and add two rules: **`main`** and **`sprint-*`**.
   (After launch you'll remove `sprint-*`.) Don't add required reviewers: CI deploys by itself.
2. **Secrets and variables → Actions → Variables → New repository variable** for each of these
   (they are variables, not secrets: none is secret):

   | Name | Value | When |
   |---|---|---|
   | `APP_HOST` | `2nd-mind.<domain>` | now |
   | `DOMAIN_NAME` | `<domain>` | now |
   | `ALERT_EMAIL` | the email the Budgets write to | now |
   | `INSTANCE_ID` | `i-…` from `scripts/tf.sh platform output -raw instance_id` | step 9 |
   | `AWS_DEPLOY_ROLE_ARN` | from `scripts/tf.sh app output -raw deploy_role_arn` | step 9 |
   | `AWS_PLAN_ROLE_ARN` | from `scripts/tf.sh app output -raw plan_role_arn` (optional: pull requests then get a plan) | step 9 |
   | `DEPLOY_SPRINT_BRANCHES` | `true` (a green push to a `sprint-*` branch deploys, until launch) | step 11 |

3. GHCR (GitHub's container registry) needs **no setup**: CI publishes the two images
   (`secondmind-api`, `secondmind-web`) with the workflow's own token. They start **private**. After
   the first CI run has pushed them (step 11), open your GitHub profile → **Packages** → each package
   → **Package settings → Change visibility → Public**. The host pulls without credentials, so a
   private package is the most common first-deploy failure.

**Check:** `gh variable list` lists the variables you've set so far. `gh api repos/<owner>/2nd-Mind/environments/production --jq .name`
prints `production`. After step 11:

```sh
docker manifest inspect ghcr.io/<owner>/secondmind-api:<tag>   # <owner> in lower case
```

prints a manifest instead of `unauthorized`.

**Why:** the deploy role trusts *only* runs from the `production` environment, and that environment
only lets `main` and `sprint-*` branches in.

---

# Part 2: building it

Ready now. From here you use the session's Terraform, and you'll start the meter: the platform apply
in step 8 creates the server, and it draws about **$18 a month** from your credit until you stop or
delete it (`make host-stop` stops most of it, before launch).

## Step 7. Terraform's state

- [ ] `make tf-bootstrap` ran, and the bucket exists.

**What Terraform's state is:** a file where Terraform remembers what it has built, so the next run
can compare. It lives in an S3 bucket, versioned (you can go back), encrypted, and closed to the
public. The bucket is the one thing Terraform can't make itself (it needs somewhere to keep state
before it can make anything), so a script makes it.

**Where:** your terminal.

**Do:**

```sh
aws sso login --profile secondmind      # if your session has expired
make tf-bootstrap
```

**Check:**

```sh
aws s3 ls --profile secondmind | grep tfstate
aws s3api get-bucket-versioning --bucket secondmind-tfstate-<account-id> --profile secondmind
```

The first prints `secondmind-tfstate-<account-id>`; the second `"Status": "Enabled"`. In the console:
search bar → **S3** → the bucket → **Properties** (versioning on, default encryption on) and
**Permissions** (Block all public access: **On**).

**Why:** the plan and every apply read and write this bucket. **Draws credit?** Cents.

**If it fails:** `The SSO session ... has expired`: run `aws sso login --profile secondmind` again.
`BucketAlreadyOwnedByYou` is fine: it already exists.

## Step 8. The platform: the host, its network and the Budgets

- [ ] `make tf-apply ROOT=platform` finished with no errors.
- [ ] The DNS record is added and `dig` shows the Elastic IP.
- [ ] I've looked inside the host through Session Manager.

**What's in the platform root:** a small private network (a VPC with one public subnet and no NAT
gateway), a firewall that only lets ports 80 and 443 in (no SSH at all), the server (an arm64 Ubuntu
24.04 `t4g.small`), an Elastic IP, an encrypted root disk and a separate encrypted data disk for the
database, daily snapshots of that data disk (7 kept), a role that lets the server read its own
settings from Parameter Store, write backups and send logs, a log group, and the two Budgets. It
holds nothing specific to 2nd Mind's app: a later project can share it.

**Do:**

1. Give Terraform your values:

   ```sh
   cp infra/terraform/platform/terraform.tfvars.example infra/terraform/platform/terraform.tfvars
   ```

   Edit `infra/terraform/platform/terraform.tfvars` (it's gitignored): `domain_name = "<domain>"`,
   `alert_email`, `github_repo = "<owner>/2nd-Mind"`. Leave `repo_ref` on the sprint branch until
   the sprint is merged (the host fetches its setup scripts from that branch at first boot); after
   the merge use `"main"`. (For DuckDNS in step 5c set `domain_name = "duckdns.org"` and add
   `app_subdomain = "yourname"`.)

2. Initialise and plan. **A plan changes nothing**: it shows what Terraform *would* do.

   ```sh
   make tf-init ROOT=platform
   make tf-plan ROOT=platform
   ```

   **How to read a plan.** Each resource is listed with a symbol: `+` will be created, `~` changed
   in place, `-` destroyed, `-/+` replaced. `(known after apply)` means AWS chooses the value. The
   last line is the summary. For this first plan it should say **`Plan: 24 to add, 0 to change, 0 to
   destroy.`** Skim for these: an `aws_instance` with `instance_type = "t4g.small"`;
   `http_tokens = "required"` and `http_put_response_hop_limit = 1`; volumes with `encrypted =
   true`; two `aws_budgets_budget`; two ingress rules, ports 80 and 443. If the numbers differ a
   little, that's fine: what matters is **0 to destroy** on a first plan and nothing you don't
   recognise (no NAT gateway, load balancer, database).

3. Apply.

   ```sh
   make tf-apply ROOT=platform
   ```

   It prints the plan again and asks `Enter a value:`. Type `yes`. It takes a few minutes. **This
   starts the credit draw** (about $18 a month).

4. **Take a tour of what was made** (console, search bar in brackets):
   - **VPC** (`VPC`) → *Your VPCs*: `secondmind-vpc`; *Subnets*: one; *Internet gateways*: one; no NAT
     gateways.
   - **Security group** (`EC2` → *Security Groups*): `secondmind-web`, inbound 80 and 443 only.
   - **Instance** (`EC2` → *Instances*): `secondmind`, `t4g.small`, with an IAM role, no key pair.
   - **Elastic IP** (`EC2` → *Elastic IPs*): one, associated with the instance.
   - **Volumes** (`EC2` → *Volumes*): a root volume and `secondmind-data`, both encrypted.
   - **Snapshot policy** (`EC2` → *Lifecycle Manager*): daily, retain 7.
   - **IAM role** (`IAM` → *Roles* → `secondmind-host`): open its permissions. It can read parameters
     under `/secondmind/prod`, write to the backup bucket, send logs, and do what the SSM agent
     needs. That is all.
   - **Budgets** (`Budgets`): `secondmind-net-monthly` ($1) and `secondmind-gross-monthly` ($25), plus
     the zero-spend one from step 2f.

5. **Add the DNS record.**

   ```sh
   scripts/tf.sh platform output dns_record
   scripts/tf.sh platform output -raw elastic_ip
   ```

   In Cloudflare: `<domain>` → **DNS → Records → Add record**: type **A**, name `2nd-mind`, IPv4
   address the `elastic_ip`, **Proxy status: DNS only (the grey cloud)**, TTL Auto → **Save**.
   **It must be grey.** A proxied (orange) record hides the address and stops Caddy getting its
   certificate.

   **Check:**

   ```sh
   dig +short 2nd-mind.<domain>
   ```

   prints the Elastic IP (a proxied record would print a Cloudflare address instead).

6. **A first look inside the host** (give it 5 minutes after the apply: first boot installs Docker
   and clones the repository).

   ```sh
   aws ssm start-session --target "$(scripts/tf.sh platform output -raw instance_id)" --profile secondmind
   ```

   You get a shell on the server with no SSH and no open port. Try:

   ```sh
   sudo tail -n 30 /var/log/secondmind-first-boot.log   # ends with "host setup done"
   free -h                                              # about 2 GB of memory and 2 GB of swap
   df -h /srv/secondmind                                # the data disk, mounted
   docker ps                                            # empty: the first deploy comes in step 11
   exit
   ```

**Check:** `aws ec2 describe-instances --profile secondmind --filters Name=tag:project,Values=secondmind --query 'Reservations[].Instances[].[InstanceId,State.Name,InstanceType]'`
prints your instance, `running`, `t4g.small`.

**Why:** the whole host is code, so you can rebuild or delete it. **Draws credit:** about $12.3 for
the instance, $3.65 for the address, $1.8 for the disks, $0.5 for snapshots, a month.

**If it fails:**
- *`Error: creating EC2 Instance ... not eligible for the Free plan`*: tell the session with the exact
  message; the instance type is a decision to make, not a setting to change quietly.
- *`Failed to get existing workspaces` / `NoSuchBucket`* on init: step 7 didn't run, or the wrong
  profile is active (`aws sts get-caller-identity`).
- *No `2nd-mind` name resolves*: check the record is **A**, **DNS only**, and the name is `2nd-mind`.
- *`aws ssm start-session` says the target isn't connected*: wait a few minutes; see *The SSM agent
  isn't online* below.

## Step 9. The app: the backup bucket, the deploy document and CI's access

- [ ] `make tf-apply ROOT=app` finished with no errors.
- [ ] The three role and instance repository variables are set.

**What's in the app root:** the private backup bucket (encrypted, versioned, dumps expire after 30
days), the SSM document that deploys a release (`secondmind-deploy`), and the way GitHub Actions
gets into AWS with **no stored key**: GitHub's OIDC identity provider and two roles.

**Do:**

```sh
cp infra/terraform/app/terraform.tfvars.example infra/terraform/app/terraform.tfvars
# edit github_repo = "<owner>/2nd-Mind"
make tf-init ROOT=app
make tf-plan ROOT=app        # expect: Plan: 13 to add, 0 to change, 0 to destroy
make tf-apply ROOT=app
```

Then copy three values into GitHub (step 6c's table):

```sh
scripts/tf.sh platform output -raw instance_id       # -> INSTANCE_ID
scripts/tf.sh app output -raw deploy_role_arn        # -> AWS_DEPLOY_ROLE_ARN
scripts/tf.sh app output -raw plan_role_arn          # -> AWS_PLAN_ROLE_ARN (optional)
```

or set them from the terminal: `gh variable set INSTANCE_ID --body "i-…"`.

**The deploy role's trust policy, line by line.** Open it: search bar → **IAM** → *Roles* →
`secondmind-ci-deploy` → **Trust relationships**.

```json
{
  "Effect": "Allow",
  "Principal": { "Federated": "arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com" },
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {
    "StringEquals": {
      "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
      "token.actions.githubusercontent.com:sub": "repo:<owner>/2nd-Mind:environment:production"
    }
  }
}
```

- `Principal.Federated` is *who may knock*: not a person or an account, but GitHub's token issuer,
  which AWS has been told to trust. Nobody with an AWS login can assume this role.
- `sts:AssumeRoleWithWebIdentity` is *how*: by presenting a signed token from that issuer.
- `aud` says the token was made *for AWS* (so a token GitHub made for something else is refused).
- `sub` is the important one: the token names **which repository and which environment** the running
  workflow belongs to. Only a job in `<owner>/2nd-Mind` running in the **`production` environment**
  matches. A fork, another repository, or a job in the same repository without that environment is
  refused. And that environment only accepts `main` and `sprint-*`, so a random branch can't deploy.
- The role's permissions (the *Permissions* tab) are equally narrow: `ssm:SendCommand` for the one
  document `secondmind-deploy`, on instances tagged `project=secondmind`, and a few read-only calls to
  watch the command's progress. It can't do anything else in the account.

**Check:**

```sh
aws iam get-role --role-name secondmind-ci-deploy --profile secondmind --query Role.AssumeRolePolicyDocument
aws ssm describe-document --name secondmind-deploy --profile secondmind --query 'Document.Status'
```

The first prints the policy above; the second `"Active"`.

**Why:** no AWS key exists anywhere: a workflow trades GitHub's short-lived token for AWS
credentials that last an hour. **Draws credit:** cents.

**If it fails:** *`EntityAlreadyExists` for the OIDC provider*: this account already has GitHub's
provider (another project used it). Set `create_oidc_provider = false` in
`infra/terraform/app/terraform.tfvars` and apply again.

## Step 10. Secrets

- [ ] `.env.prod` is filled in and passes `PROD_SECRETS_DRY_RUN=1 make prod-secrets`.
- [ ] `make prod-secrets` wrote the parameters.

**What Parameter Store is:** a place in AWS Systems Manager that keeps settings, and, encrypted as
*SecureString*, secrets. The host reads them at deploy time (the instance role allows only
`/secondmind/prod/*`). **No secret is ever in Terraform or its state** or the repository: a script
writes them from a file on your laptop.

**Do:**

```sh
cp .env.prod.example .env.prod          # gitignored; never commit it
```

Fill in `.env.prod`. For the random ones:

```sh
openssl rand -hex 32     # SESSION_SECRET
openssl rand -hex 6      # ACCESS_CODE: the code you'll give people, until S5 opens the site
openssl rand -hex 24     # each of POSTGRES_PASSWORD, APP_DB_PASSWORD, REDIS_PASSWORD
```

The provider keys and the Langfuse values come from step 6; `APP_HOST` and `ALLOWED_ORIGINS` are
`2nd-mind.<domain>` and `https://2nd-mind.<domain>`; `IMAGE_REGISTRY` is `ghcr.io/<owner>` **in lower
case**. Leave `GUESTS_OPEN=false`. A value can't contain a single quote or a newline. Check the file
without writing anything, then write it:

```sh
PROD_SECRETS_DRY_RUN=1 make prod-secrets
make prod-secrets
```

The check refuses a name that isn't in `.env.prod.example` (a typo), a missing secret, and a value
with a quote. **Check:**

```sh
aws ssm get-parameters-by-path --path /secondmind/prod/ --profile secondmind \
  --query 'Parameters[].[Name,Type]' --output table
```

lists every name with `SecureString` for the secrets and `String` for the rest (values are not
shown, because there's no `--with-decryption`). In the console: search bar → **Parameter Store**
(under Systems Manager) → **My parameters**.

**Why:** the deploy script turns these into the environment file the containers see. The mapping of
every variable to its source is `docs/deploy/config.md`. **Draws credit:** nothing (standard
parameters are free).

## Step 11. The first deploy

- [ ] CI is green on the sprint branch on GitHub.
- [ ] Both GHCR packages are public.
- [ ] The deploy workflow ran, and `https://2nd-mind.<domain>` asks for the access code.

**What happens on a deploy:** CI builds arm64 images, scans them, and pushes them to GHCR tagged with
the git SHA. Then the deploy workflow, from the `production` environment, sends the
`secondmind-deploy` document to the server over SSM. On the server: check out that SHA, pull the
images, write the environment file from Parameter Store, run the migration once, start the stack,
and wait for `/readyz` to answer from *this* release. If the migration fails, nothing changes; if the
release doesn't become ready, the previous one is put back.

**Do:**

1. In GitHub, set the variable **`DEPLOY_SPRINT_BRANCHES` = `true`** (until launch, a green push to a
   `sprint-*` branch deploys).
2. Make sure the branch is pushed and CI is green: `gh run list --branch sprint-4-production-and-links --limit 3`.
   The *images* job pushes `secondmind-api` and `secondmind-web` to GHCR on a `sprint-*` push.
3. **The first time only:** the new packages are private, so the deploy's first check
   (*the images for this release exist on GHCR*) fails. Make both public (step 6c), then re-run the
   failed job: `gh run rerun <run-id> --failed`, or `make prod-deploy SHA=$(git rev-parse HEAD)` to
   deploy the same commit straight over SSM without GitHub.
4. **Follow it** in GitHub → **Actions → ci → deploy**, and in AWS: search bar → **Systems Manager** →
   **Run Command** → **Command history** → the newest command → **Output**. You'll see `==> pull`,
   `==> migrate`, `==> up`, `release <sha> is serving`.
5. **Watch the certificate get issued.** Caddy asks Let's Encrypt for a certificate the first time it
   is asked for the name; it needs port 80 open and the DNS record grey. In a Session Manager shell
   (step 8):

   ```sh
   docker logs secondmind-caddy-1 2>&1 | grep -i -E "certificate|obtained|error" | tail
   ```

   `certificate obtained successfully` means it worked (about a minute).
6. Open `https://2nd-mind.<domain>`. It asks for an email and the access code (the `ACCESS_CODE` from
   `.env.prod`). Enter both.

**Check:**

```sh
scripts/check-deployed.sh https://2nd-mind.<domain>
```

prints `all checks passed` (liveness, readiness, the release, security headers on the app and the
API, and that a cross-site request is refused): it makes no model calls.

**Why:** from now on, deploying is CI's job. **If it fails:** the troubleshooting section, in this
order: *no certificate*, *the SSM agent isn't online*, *AssumeRoleWithWebIdentity denied*, *image pull
denied*.

## Step 12. Checking it

- [ ] `make smoke-prod` passes.
- [ ] A production trace is in Langfuse with no message text.
- [ ] A production turn's logs are in CloudWatch.
- [ ] The Budgets are green and the Credits page shows this month's draw.

**Do:**

1. **The smoke test**, on real models (about a cent or two):

   ```sh
   make smoke-prod
   ```

   It reads the URL and access code from `.env.prod`, runs the checks from step 11, then the
   `@prodlike` browser tests: the access door, save, recall and undo on real models, the headers.
   It prints what it cost and a run id.
2. **The headers:** `curl -sI https://2nd-mind.<domain>/ | grep -i -E "strict-transport|content-security|x-frame"`.
3. **Langfuse:** open your project → **Traces**. Open the newest: you see the steps with timing and
   token counts, and **no message text**. In the app, open the glass box on a turn: its trace link
   opens the same trace.
4. **Logs:** search bar → **CloudWatch** → **Log groups** → `/secondmind/prod` → a log stream per
   container (`secondmind-api-1` and so on). They're JSON, kept 7 days, and never contain what you
   said.
5. **Budgets:** search bar → **Budgets**: three budgets, all *OK*.
6. **Credits:** **Billing and Cost Management → Credits**: the remaining credit, and the month's
   charges against it. **Billing → Bills** should show **$0 charged**, with the draw netted off by
   credit. The first full day's draw should be about **$0.60** or less.

**Check:** everything above is the check: the smoke test passes, the trace has no message text, the
log group has streams, the Budgets are green and the Credits page shows a draw on plan.

**Why:** you've now seen every part of the system working from outside. **Draws credit:** the
smoke run costs about $0.02 of model credit, not AWS credit.

**If it fails:** a failed browser test leaves a report in `frontend/playwright-report/`. If the door
test fails, check `ACCESS_CODE`. If a turn fails with a provider error, look at
`make prod-admin CMD="spend"` (a provider credit limit may be too low).


---

# Part 3: running it

Exercises you do once, so the first time you need one for real it isn't the first time. Each links to
its section of the [runbook](../runbook.md), the short version for later.

## Exercise 1. Deploy a change

- [ ] I changed something visible and watched it deploy. [Runbook §5](../runbook.md#5-deploy)

**Do:** change the page title in `frontend/index.html`, commit it (`feat(web): …` with the repository's
commit standard), and push the sprint branch. Watch **Actions**: CI runs, then *deploy*.
**Check:** reload the site and the tab shows the new title; `curl -s https://2nd-mind.<domain>/v1/meta`
reports the new short SHA under `"version"`. **Why:** this is the whole loop: change, push, live,
without touching AWS.

## Exercise 2. Roll back, and roll forward

- [ ] I rolled back to the previous release and forward again. [Runbook §6](../runbook.md#6-roll-back)

**Do:** find the previous release's SHA (`git log --oneline -3`), then redeploy it without running
migrations:

```sh
make deploy SHA=<previous-sha>            # once the deploy workflow is on main
make prod-deploy SHA=<previous-sha>       # the same, straight over SSM, works from any branch
```

(`make deploy` asks GitHub to run the workflow, and GitHub lists a workflow only once it's on the
default branch; `make prod-deploy` sends the same SSM document with your own login, and is also the
way in if GitHub is down.) **Check:** `curl -s https://2nd-mind.<domain>/v1/meta` shows the old SHA.
Roll forward the same way with the newer SHA. **Why not run migrations:** a release is written to
work with the schema of the release before it, so a rollback never needs a schema downgrade. A
release *with* a migration is deployed with `MIGRATE=true` (the default when you don't name a SHA).

## Exercise 3. Flip the kill switch

- [ ] I stopped and resumed all model calls in production. [Runbook §1](../runbook.md#1-kill-switch)

```sh
make prod-admin CMD="kill-switch on"
```

Send a message in the app: it answers *New answers are paused for everyone right now*, with no
model call. Then:

```sh
make prod-admin CMD="kill-switch off"
make prod-admin CMD="spend"
```

**Why:** this is the panic button if something spends money it shouldn't. It runs the app's admin
tool inside the api container, over SSM, with your login. It needs no deploy and no restart.

## Exercise 4. Restore last night's dump into your laptop

- [ ] I restored a production dump into the local stack. [Runbook §7](../runbook.md#7-restore-the-database)

**Do:** the timer dumps the database at 02:30 IST. To have one now:
`scripts/prod-ssm.sh shell 'sudo /opt/secondmind/infra/host/backup.sh'`. Then:

```sh
aws s3 ls s3://secondmind-backups-<account-id>/dumps/ --recursive --profile secondmind
aws s3 cp s3://secondmind-backups-<account-id>/dumps/<year>/<month>/<file>.dump ./last.dump --profile secondmind
make up
make restore-local DUMP=./last.dump
```

**Check:** open http://localhost:8080 and sign in with an email you used on the production site: your
memories are there. **Why:** a backup you haven't restored is a hope. The dump is a normal
PostgreSQL custom-format dump; the restore script recreates the database and the app's database role
first. (`make restore-local` replaces the local database: run it on a stack you can lose.)

## Exercise 5. Find one turn's logs

- [ ] I found every log line of one turn. [Runbook §9](../runbook.md#9-the-host)

**Do:** in the app, send a message and open its glass box: note the turn's id. Then in the console:
**CloudWatch → Logs Insights**, select the log group `/secondmind/prod`, a time range of the last
hour, and run:

```
fields @timestamp, @message
| filter @message like /<turn-id>/
| sort @timestamp asc
```

**Why:** the trace id is the turn id, so the same id finds the Langfuse trace and the log lines. The
logs record what happened (step names, costs, timings), never what you said.

## Exercise 6. Apply updates and reboot

- [ ] I checked for updates and rebooted, and the site came back. [Runbook §9](../runbook.md#9-the-host)

The host installs security updates by itself (`unattended-upgrades`) and reboots in a weekly window
(Sunday 03:30 IST) only when an update needs it. To look, and to do it by hand:

```sh
scripts/prod-ssm.sh shell 'apt list --upgradable 2>/dev/null | head; cat /var/run/reboot-required 2>/dev/null || echo "no reboot needed"; systemctl list-timers "secondmind-*" --no-pager'
scripts/prod-ssm.sh shell 'shutdown -r +1'
```

Wait about two minutes, then `scripts/check-deployed.sh https://2nd-mind.<domain>`. **Why:** the site
must come back by itself after a reboot: the data disk mounts before Docker starts, and every
container restarts on its own.

## Exercise 7. Stop the host and start it again

- [ ] I stopped and started the host and saw what still draws credit. [Runbook §9](../runbook.md#9-the-host)

```sh
make host-stop
make host-status
```

The site is down. In the console (**EC2**) the instance is *Stopped*, but the **Elastic IP** is still
allocated and both **volumes** exist: those keep drawing (about $5.60 a month together) while the
instance's $12.3 stops. Then:

```sh
make host-start
scripts/check-deployed.sh https://2nd-mind.<domain>
```

**Why:** before launch nobody visits, so stopping the host when you aren't working stretches the
credit by more than a third. After launch the site can't sleep.

## Exercise 8. Read the Credits page, and work out when the credit ends

- [ ] I know which month my credit runs out.

**Do:** **Billing and Cost Management → Credits**: note the **remaining** amount and the date you're
reading it. Read a full day's or week's draw from **Bills** or **Cost Explorer** (the first time you
open Cost Explorer it can take a day to have data). Then:

```
months left = remaining credit ÷ (daily draw × 30)
```

For example $190 left at $0.60 a day is about 10.5 months. Compare it with the Free plan's hard stop
at month 6: **whichever comes first ends your time on AWS.** Write the answer in the step 2g
checklist. **Why:** the plan only works if you decide to move (Part 4) before either one arrives.

---

# Part 4: leaving, before month 5

## Tearing it all down

Use this to stop the meter completely, or before moving. **Order matters**, because the app root
depends on the platform:

1. **Save what you want.** Download the last dump (Exercise 4). Take a final snapshot of the data
   disk:
   `aws ec2 create-snapshot --volume-id "$(scripts/tf.sh platform output -raw data_volume_id)" --description "final" --profile secondmind`.
2. **Empty the backup bucket** (versioning means `rm` isn't enough). In the console: **S3** → the
   bucket → **Empty**. Or `aws s3 rm s3://secondmind-backups-<account-id> --recursive --profile secondmind`
   and delete the remaining versions from the **Versions** view.
3. **Destroy the app root, then the platform:**

   ```sh
   scripts/tf.sh app destroy
   scripts/tf.sh platform destroy
   ```

4. **Delete the parameters:** `aws ssm delete-parameters --profile secondmind --names $(aws ssm get-parameters-by-path --path /secondmind/prod/ --profile secondmind --query 'Parameters[].Name' --output text)`.

**What survives the destroy:** the **state bucket** (it isn't in Terraform; delete it last, by hand, if
you're finished), the **snapshots** (kept until deleted: a final one costs cents a month), the
**IAM Identity Center** setup, and the **domain and DNS**, which were never on AWS. Remove the
Cloudflare `2nd-mind` record or point it at the new host.

## The choice at month 5: move, or upgrade

| | Costs | Keeps | Gives up |
|---|---|---|---|
| **Move to Oracle Cloud Always Free** (the plan) | ₹0 in cash (the domain only) | the app, its data (by restore), the domain | learning more of AWS |
| **Upgrade AWS to the Paid plan** | about $18 a month from the credit, then from your card after month 12 | the AWS setup and the leftover credit until month 12 | the ₹0 promise once credit ends |
| **A small ARM VPS** (Hetzner, Hostinger) | about ₹400 to ₹600 a month | the app and data | the free tier; a card is charged |

Decide by the reminder date in step 2g. Whichever you pick, **the app's containers, the compose
file, Caddy, the host script, the backup format, the images (GHCR) and DNS (Cloudflare) move
unchanged.** Writing the Oracle Terraform is its own piece of work later (the sprint's ledger row 51);
this checklist is the move by hand.

## The move to Oracle Cloud Always Free, as a checklist

Oracle's catches first, because two of them can't be undone or found out late:

- **Pick the home region carefully:** it can't be changed after sign-up, and free ARM capacity can run
  out in busy regions (Mumbai and Hyderabad included). Retrying later, or a quieter region, is normal.
- **Upgrade to Pay As You Go** (still ₹0 within the free limits). Otherwise a quiet instance is
  reclaimed after 7 idle days, and this one is quiet most of the time.
- Card checks sometimes fail with Indian cards; try another card or wait.
- Oracle halved the free allowance once (15 June 2026) and could again: the VPS fallback below stays.

Then:

- [ ] Sign up at oracle.com/cloud/free; choose the home region; add a payment card; **upgrade to Pay
  As You Go**; set a Budget of a few rupees so any charge emails you.
- [ ] Create an **Ampere A1** instance (ARM): **Ubuntu 24.04**, 2 OCPU, 12 GB, a boot volume of about 50
  GB; add your SSH public key (Oracle uses SSH: this is the one place it does). Open ports 80 and 443
  in the subnet's security list. Reserve a public IP.
- [ ] On the instance: `curl -fsSL https://raw.githubusercontent.com/<owner>/2nd-Mind/main/infra/host/setup.sh | sudo bash`
  (or clone the repository and run `infra/host/setup.sh`): the same script as AWS, with no AWS part.
- [ ] Write `/etc/secondmind/host.conf` with `ENV_SOURCE=file` and put the env file (the same names as
  `.env.prod.example`, one `NAME='value'` per line) at `/etc/secondmind/prod.env`, mode 600.
- [ ] Restore the latest dump: copy it over, and load it into the new Postgres (the steps in
  `scripts/restore-local.sh`, run against the new host's compose project).
- [ ] Deploy: `sudo /opt/secondmind/infra/host/deploy.sh <sha> --no-migrate` (a restored dump is already at the
  right schema; use plain `<sha>` to migrate).
- [ ] Switch the DNS record in Cloudflare (DNS only) to the new IP. Run `scripts/check-deployed.sh
  https://2nd-mind.<domain>` and `make smoke-prod` against it.
- [ ] Then the AWS teardown above.

Deploys from CI over SSM stop working on the new host (SSM is an AWS thing): the replacement is SSH from
CI with a deploy key, part of the move's own piece of work. Until then, deploy by SSH-ing in and
running `deploy.sh`.

**The VPS fallback, in a box.** If Oracle's capacity, card check or limits get in the way: rent a small
ARM VPS (2 vCPU, 2 to 4 GB), install Ubuntu 24.04, run the same `setup.sh`, and follow the same
checklist. On an **x86** VPS the images need a `linux/amd64` build too (CI adds it): ask the session.

---

# Troubleshooting

The errors a first deploy usually hits, in the order they tend to appear.

**No certificate: DNS not delegated, or the Cloudflare record is proxied.** The site shows a browser
warning, or `curl -sI https://2nd-mind.<domain>` fails on TLS. Caddy needs `2nd-mind.<domain>` to
resolve to the Elastic IP and port 80 open. Check `dig +short 2nd-mind.<domain>`: it must be your
Elastic IP; a Cloudflare address means the record is orange (proxied): set it to **DNS only**. Check
`dig NS <domain> +short` shows Cloudflare's servers (delegation). Then read Caddy's log on the host:
`docker logs secondmind-caddy-1 2>&1 | tail -30`. Let's Encrypt limits failed attempts to a few an
hour per name: fix the cause, then wait rather than restarting in a loop.

**The SSM agent isn't online.** `aws ssm start-session` says the target isn't connected, or Run
Command says *InvalidInstanceId*. Check:
`aws ssm describe-instance-information --profile secondmind --query 'InstanceInformationList[].[InstanceId,PingStatus]'`.
An empty list, 5 minutes after boot: (1) the instance has no role (EC2 → the instance → **Security** →
IAM role should be `secondmind-host`); (2) it has no route to the internet (its subnet's route table
needs `0.0.0.0/0` to the internet gateway, and the Elastic IP must be associated); (3) first boot
is still running. Reboot the instance once from the console if it stays offline.

**`AssumeRoleWithWebIdentity` denied.** The deploy job fails at *Configure AWS credentials* with *Not
authorized to perform sts:AssumeRoleWithWebIdentity*. The token's `sub` doesn't match the role's
trust policy. Check: the job runs in the `production` environment (the branch is `main` or `sprint-*`
and the environment's rule allows it); `github_repo` in `infra/terraform/app/terraform.tfvars` is exactly
`<owner>/<name>` with the same capitalisation GitHub shows; the OIDC provider exists (step 9); and the
variable `AWS_DEPLOY_ROLE_ARN` is the **deploy** role, not the plan role.

**An image pull is denied, or the images are "missing".** The deploy stops at *the images for this
release exist on GHCR*, or the host says `denied`. The GHCR packages are still private (step 6c),
or `IMAGE_REGISTRY` in `.env.prod` isn't `ghcr.io/<owner>` in **lower case**, or CI hasn't pushed that
SHA (images are pushed only from a green run on `main` or a `sprint-*` branch). Fix the cause and
re-run the deploy job.

**The host is out of memory.** The site is slow or a container keeps restarting. On the host:
`free -h` (heavy swap use is the sign), `docker stats --no-stream`, `sudo dmesg | grep -i -E "oom|killed"`.
The stack is measured at about 0.6 GB in normal use and its limits add up to about 1.6 GB, so a
runaway is a bug worth reporting with those outputs. Levers, in order: lower the worker's concurrency,
Postgres's memory settings, the api's workers. A bigger instance is a question for the session, because
it may not be allowed on the Free plan.

**A credit task's $20 didn't show up.** Wait 10 minutes, then an hour. Check the *Explore AWS* widget:
is the activity marked complete? If you finished it but skipped a step (for example, you never
**terminated** the EC2 instance, or used the wrong service), redo it. If a task is complete and $20
is still missing after a day, contact AWS Support from the console (a free Basic-support case for
billing issues) and tell the session.

**Other things you may meet.**

- *`terraform init` fails with `NoSuchBucket`*: step 7 wasn't run, or a different profile is active.
- *`The SSO session has expired`*: `aws sso login --profile secondmind`.
- *`EntityAlreadyExists` on the OIDC provider*: `create_oidc_provider = false` (step 9).
- *`terraform apply` asks for `domain_name`*: `terraform.tfvars` isn't in `infra/terraform/platform/`.
- *The console says a service isn't available on the Free plan*: tell the session. Upgrading to the
  Paid plan lifts it but changes the billing (Part 4).
- *A `make` target says `docker: command not found` or can't connect*: start Docker (Colima or Docker
  Desktop).

---

# Glossary

Every AWS term used above, in one sentence.

- **Account**: your own container for everything you create, with its own bill.
- **Free plan / Paid plan**: the Free plan can't bill you and closes at 6 months; the Paid plan bills normally and keeps unused credit until it expires.
- **Credits**: prepaid AWS money that pays bills first; these expire 12 months after sign-up.
- **Region**: a geographic cluster of AWS data centres, such as `us-east-1` (N. Virginia).
- **Availability zone (AZ)**: one data-centre building (or group) inside a region; this app uses one.
- **VPC**: your own private network inside AWS.
- **Subnet**: a slice of a VPC's address range, in one availability zone.
- **Internet gateway**: the door that lets a VPC's public addresses talk to the internet.
- **NAT gateway**: a paid box that lets private machines reach the internet out; deliberately not used here.
- **Security group**: a firewall attached to a machine, listing what may come in and out.
- **EC2**: AWS's virtual servers.
- **Instance type**: a server size, such as `t4g.small` (2 CPUs, 2 GB, Arm).
- **AMI**: the disk image a server starts from (here, Canonical's Ubuntu 24.04).
- **Elastic IP**: a public IPv4 address that is yours and stays put when the server stops.
- **EBS volume / gp3**: a network disk for a server; gp3 is the general-purpose SSD type.
- **Snapshot**: a saved copy of a disk at one moment.
- **Data Lifecycle Manager (DLM)**: AWS's scheduler for snapshots (here: daily, keep 7).
- **IAM**: AWS's permissions system: who may do what to which resource.
- **Role**: a set of permissions that a person, service or workflow can temporarily *assume*.
- **Instance profile**: how a role is attached to an EC2 server.
- **Policy**: the JSON that lists a role's allowed actions and resources.
- **Trust policy**: a role's list of who may assume it.
- **IAM Identity Center**: AWS's sign-in service for people: one login, short-lived sessions, MFA.
- **MFA**: a second proof of identity, such as an authenticator app code.
- **STS**: the service that hands out temporary credentials for a role.
- **OIDC (OpenID Connect)**: the standard GitHub uses to prove a workflow's identity to AWS without a stored key.
- **ARN**: the unique name of any AWS thing, like `arn:aws:iam::123456789012:role/name`.
- **Systems Manager (SSM)**: AWS's toolbox for managing servers, including the three next entries.
- **Session Manager**: a shell on a server through AWS, with no SSH or open port.
- **Run Command**: sending a command or script to servers from AWS (how CI deploys).
- **Parameter Store**: settings and secrets kept by SSM; a *SecureString* is encrypted.
- **KMS**: AWS's key service; the default key encrypts SecureString parameters.
- **S3 / bucket**: AWS's object storage; a bucket is a named container of files.
- **Versioning**: S3 keeps every older copy of an object when it's overwritten or deleted.
- **Lifecycle rule**: an S3 rule that expires objects after some days (dumps go after 30).
- **CloudWatch / log group**: where AWS collects logs; a log group holds the streams of one application.
- **Budgets**: AWS's spending alarms that email you at a threshold.
- **IMDS / IMDSv2**: the address a server asks for its own role credentials; v2 with a hop limit of 1 keeps containers from reaching it.
- **Terraform**: a tool that builds infrastructure from files, so it can be recreated or deleted.
- **State**: Terraform's record of what it has built, kept here in S3.
- **Provider / module**: a provider teaches Terraform an API (AWS); a module is a reusable group of resources.
- **Plan / apply**: `plan` shows what Terraform would change; `apply` does it.
- **GHCR**: GitHub's container registry, where the images live (public, so the host pulls without credentials).
- **Container registry**: a store of container images, addressed by name and tag.
- **Access code**: 2nd Mind's own gate: every way into the site asks for it until the site opens to guests.
