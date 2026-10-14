---
title: Bank Transaction Analytics
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
short_description: Search, fraud cases and CSV scoring on 6.36M transactions
---

# Bank Transaction Analytics and Fraud Detection

Big Data Analytics coursework. Apache Spark (Spark SQL, MLlib) processes the 6.36M-row
[PaySim](https://www.kaggle.com/datasets/ealaxi/paysim1) dataset. This Space runs the full dashboard:
millisecond transaction search (DuckDB), fraud case management (MongoDB Atlas) and CSV upload with
batch scoring by the Random Forest trained in Spark MLlib.

Source code, pipeline and report: https://github.com/JayQuery-007/bank_transaction_analysis

Dataset: Lopez-Rojas, Elmir and Axelsson, "PaySim: a financial mobile money simulator for fraud
detection", EMSS 2016.
