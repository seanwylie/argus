# Security

## Reporting a vulnerability

Please report privately, so a fix can exist before details are public.

Use GitHub's private vulnerability reporting:
**[open a draft advisory](https://github.com/seanwylie/argus/security/advisories/new)**. That
route is preferred over email; there is no published address for this project.

The draft-advisory form only works when
[private vulnerability reporting](https://docs.github.com/code-security/security-advisories/working-with-repository-security-advisories/configuring-private-vulnerability-reporting-for-a-repository)
is enabled on the repository. If the form is missing, do not open a public issue.

Please do not open a public issue for a vulnerability, and please do not post exploit material
in public threads.

**Expect a slow response.** This project is maintained as time permits and carries no
production-support commitment. There is no service-level agreement on triage or fixes. If you
need a guaranteed timeline, this is not a suitable dependency.

## What is in scope

The **local Argus CLI** and its normal operation:

- reading and writing under the repository, including `runs/`
- the approval, capability, and autonomy gates that decide whether an action may run
- the execution sandbox and the working-directory restriction
- Builder's path-scope contract and its breach detection
- optional subprocess execution, including invocation of an external agent

A finding that lets any of the following happen is in scope and worth reporting:

- an action executing without passing the approval or capability gate
- a write landing outside a contract's declared allowed paths without being reported as a breach
- the sandbox being escaped, or silently degrading without that degradation being recorded
- a credential or secret being written into an artifact under `runs/`

## What is out of scope

- **Deployments, hosted services, and infrastructure.** None exist. There is no server, no
  hosted endpoint, and no deploy pipeline to attack.
- **Third-party services.** No integrations are implemented. Report issues in another project's
  code to that project.
- **Behavior of the external coding agent.** Builder invokes an agent binary that this project
  does not ship or control. Containment failures are in scope; what the agent chooses to do
  inside its allowed paths is not.
- **Weaker containment on non-Linux hosts.** This is known, documented, and reported at runtime
  rather than hidden. OS-level containment relies on Linux facilities (bubblewrap, and Landlock
  where applied). A report that the *degradation is silent* would be in scope; that it exists is
  not.
- **Anything requiring the operator to have already granted permission.** The gates are the
  security boundary. Convincing an operator to approve a harmful action is a UX concern, not a
  vulnerability, unless the request misrepresented what would happen.

## Hardening notes for operators

- Nothing runs against a product tree without an approved action; the read-only spine is the
  default and executing is opt-in.
- `runs/` is local and mostly gitignored. Treat it as potentially sensitive: it records what was
  observed on your machine.
- LLM paths are off by default. Enabling them sends content to a third party, so review what
  your products contain first.
- Run `argus doctor` to see what containment your host can actually enforce before enabling
  execution.

## No guarantees

This software is provided **as-is**, with **no warranties** and **no guarantees** of security,
correctness, or fitness for any purpose. It is an experiment published for study. See
[LICENSE](LICENSE).
