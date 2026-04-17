# Monitoring & Alerting Thresholds
**Date:** 23 March 2026  
**Time:** 15:10  

---

## 1. Health, Availability & Performance

| No. | Category | Target | Threshold | Severity |
|-----|----------|--------|-----------|----------|
| 1 | Health & Availability | Web Application | 3 consecutive failures | SEV1 |
| 2 | API Endpoint | - | 3 consecutive failures | SEV1 |
| 3 | DB Connection | - | 3 consecutive failures | SEV1 |
| 4 | External System Integration | - | 3 consecutive failures | SEV1 |
| 5 | Generative AI API Connectivity | - | 3 consecutive failures | SEV1 |
| 6 | Application Performance (Without LLM) | Response Time | Target exceeded for 10 mins | SEV2 |
| 6a | Application Performance (Without LLM) | Response Time | 2x target exceeded for 10 mins | SEV1 |
| 7 | Application Performance (With LLM) | Response Time | Target exceeded for 10 mins | SEV2 |
| 7a | Application Performance (With LLM) | Response Time | 2x target exceeded for 10 mins | SEV1 |
| 8 | HTTP 5xx Error Rate | - | ≥1% for 5 mins | SEV2 |
| 8a | HTTP 5xx Error Rate | - | ≥5% for 5 mins | SEV1 |
| 9 | Exception Count | - | 2x increase vs previous week | SEV3 |

---

## 2. Infrastructure & Resource Monitoring

| No. | Category | Target | Threshold | Severity |
|-----|----------|--------|-----------|----------|
| 10 | Concurrent Connections | - | 90% of capacity | SEV3 |
| 10a | Concurrent Connections | - | Capacity exceeded | SEV2 |
| 11 | CPU Utilization | - | ≥80% for 15 mins | SEV2 |
| 11a | CPU Utilization | - | ≥95% for 10 mins | SEV1 |
| 12 | Memory Utilization | - | ≥80% for 15 mins | SEV2 |
| 13 | Memory Pressure | - | On detection | SEV1 |
| 14 | Storage Utilization | - | ≥80% | SEV3 |
| 14a | Storage Utilization | - | ≥90% | SEV2 |
| 15 | DB Connections | - | ≥85% of capacity | SEV3 |
| 16 | DB Connection Exhaustion | - | Connection failure | SEV1 |
| 17 | Batch Jobs | Abnormal Termination | On detection | SEV1 |
| 18 | Batch Completion | Time Limit | Time exceeded | SEV1 |

---

## 3. Non-Functional / AI & System Monitoring

| No. | Category | Target | Threshold | Severity |
|-----|----------|--------|-----------|----------|
| 19 | Execution Time | - | Expected time +20% | SEV2 |
| 20 | Ingestion Count | - | ±30% vs previous day | SEV2 |
| 21 | External Integration Timeout Rate | - | ≥3% | SEV2 |
| 21a | External Integration Timeout Rate | - | ≥10% | SEV1 |
| 22 | Consecutive Failures | - | 5 consecutive | SEV2 |
| 22a | Consecutive Failures | - | 10 consecutive | SEV1 |
| 23 | Response Time Increase | - | 2x normal for 10 mins | SEV2 |
| 24 | Generative AI Call Failure Rate | - | ≥3% | SEV2 |
| 24a | Generative AI Call Failure Rate | - | ≥10% | SEV1 |
| 25 | Timeout Rate | - | ≥3% | SEV2 |
| 25a | Timeout Rate | - | ≥10% | SEV1 |
| 26 | Average Token Count | - | +50% vs previous week | SEV3 |