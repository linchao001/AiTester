"""用例设计专属 loop（case_design_loop）的领域包。

包根不 import 任何子模块：`orchestration.graph_registry` 会 import 本包 graph.py，
包根转发会在 import 链上绕环（与 orchestration 包根同源约束）。
"""
