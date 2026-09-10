# Architecture Decision Records

This directory records durable product and implementation decisions for ECatVASP. For accepted development/release baselines and distribution status, see [`../../CHANGELOG.md`](../../CHANGELOG.md).

1. Scientific Core Architecture
2. Domain Schema and Identity
3. Scientific Workflow DAG
4. Recipe and Preflight Boundary
5. Freeze and Contract
6. Structure Import / Export Boundary
7. Graphene Builder Boundary
8. Vacancy / Dopant Builder Boundary
9. Single Metal Site Boundary
10. Dual / Triple Metal Site Boundary
11. ActiveSite Tooling Boundary
12. Adsorbate Builder Boundary
13. Adsorption Structure Validation Boundary
14. Model Studio Final Acceptance Boundary
15. VASP Pipeline Domain Contracts
16. POSCAR and Permanent Atom Identity Boundary
17. INCAR Generation Boundary
18. KPOINTS Generation Boundary
19. POTCAR Identity and Materialization Boundary
20. VASP Input Materialization Boundary
21. Calculation Recipe Registry Boundary
22. Preflight Validation Boundary
23. Scientific Calculation Plan Boundary
24. VASP Pipeline Final Acceptance Boundary
25. HPC Execution Domain Contracts and Schema v2 Boundary
26. SSH / Slurm Submission Boundary
27. Scheduler Monitoring and Retrieval Boundary
28. Retry and Recovery Boundary
29. v0.4 Final Execution Acceptance and Handoff Boundary
30. v0.5 Scientific Result Parsing Boundary
31. v0.5 Result Artifact Intake and Integrity Boundary
32. v0.5 Energy and Metadata Parser Boundary
33. v0.5 Scientific Convergence Classification Boundary
34. v0.5 Final Forces and Magnetization Boundary
35. v0.5 CONTCAR Reconstruction and Structure Promotion Boundary
36. v0.5 Frequency Scientific Results Boundary
37. v0.5 Result Provenance, Freshness, and Existing-Import Unification Boundary
38. v0.5 Final Scientific Result Acceptance and Hardening Boundary
39. v0.6 Scientific Workflow Domain Contracts and Schema v3 Boundary
40. v0.6 Canonical Workflow Recipe Registry Boundary
41. v0.6 Deterministic Workflow Planning Boundary
42. v0.6 Step Materialization and Structure Binding Boundary
43. v0.6 Scientific Gates, Freshness, and Supersession Boundary
44. v0.6 Workflow Recovery and Continuation Policy Boundary
45. v0.6 Orchestration Reconciliation and Execution/Materialization Handoff Boundary
46. v0.6 Durable Workflow Reopen, Resume, and Idempotency Boundary
47. v0.6 Final Workflow Acceptance and Hardening Boundary
48. v0.7 Electronic Structure and Analysis Architecture Boundary
49. v0.7 DOS/PDOS Canonical Intake Boundary
50. v0.7 Durable DOS/PDOS Analysis Materialization Boundary
51. v0.7 Bader Analysis Intake and Provenance Boundary
52. v0.7 Charge-Density Difference Analysis Boundary
53. v0.7 LOBSTER / COHP / ICOHP Result-Intake Boundary
54. v0.7 Electronic Descriptor Boundary
55. v0.7 Electronic Analysis Reconciliation and Workflow Integration Boundary
56. v0.7 Final Electronic Analysis Acceptance and Hardening Boundary
57. v0.8 Thermochemistry and Electrocatalysis Free-Energy Architecture Boundary
58. v0.8 Thermochemistry Domain Contracts Boundary
59. v0.8 Harmonic Surface / Adsorbate Thermochemistry Boundary
60. v0.8 Ideal-Gas Reference Thermochemistry Boundary
61. v0.8 Explicit Reference Correction Policy Boundary
62. v0.8 CHE Potential and pH Semantics Boundary
63. v0.8 Generic Reaction and Pathway Stoichiometry Boundary
64. v0.8 Potential-Dependent Electrocatalysis Descriptor Boundary
65. v0.8 Durable Reaction Diagram and Reconciliation Boundary
66. v0.8 Final Thermochemistry and Electrocatalysis Acceptance and Hardening Boundary
67. v0.9 Research Workspace Scientific Presentation Architecture Boundary
68. v0.9 Workspace Projection Contracts Boundary
69. v0.9 Scientific Inventory and Provenance Inspection Boundary
70. v0.9 Workflow and Execution Readiness Dashboard Boundary
71. v0.9 Scientific Visualization Presentation Datasets Boundary
72. v0.9 Scientific Reporting Export Boundary
73. v0.9 Experiment-like Application Services Boundary
74. v0.9 Headless CLI and Python Application Facade Boundary
75. v0.9 Frontend Handoff and Workspace Interoperability Boundary
76. v0.9 Final E2E Acceptance and Hardening Boundary
77. v1.0 Production Desktop Workspace Architecture Boundary
78. v1.0 Local Desktop Backend Host Lifecycle Boundary
79. v1.0 Tauri/Svelte Desktop Shell and Typed Client Boundary
80. v1.0 Desktop Project Lifecycle and Local Preferences Boundary
81. v1.0 Desktop Scientific Workspace Views Boundary
82. v1.0 Typed Desktop Application Actions Boundary
83. v1.0 Windows Sidecar Packaging Boundary
84. v1.0 Desktop Resilience, Security, Diagnostics, and Exports Boundary
85. v1.0 Final Desktop E2E Acceptance and Hardening Boundary
86. v1.1 Research Workflow UX and Closed-loop Productivity Boundary
87. v1.1 Electronic Analysis Workspace Boundary
88. v1.1 Thermochemistry and Reaction Workspace Boundary
89. v1.1 Large-project Performance and Product Hardening Boundary
90. v1.1 Installed Desktop Scientific E2E Acceptance Boundary
91. v1.2 Architecture Baseline and Dependency Audit Boundary
