"""reme 插件包（entry point `aitester`）：只提供 plugin.yaml 的 backends 注册。

step 实现在 aitester.services.kb.steps（与 manager/config 同处 KB 服务包）；
本包刻意不放代码——plugin.yaml 才是契约面（package-only entry point 形态）。
"""
