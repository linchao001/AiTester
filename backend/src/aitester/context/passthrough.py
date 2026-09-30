class PassthroughContextBuilder:
    def build(
        self,
        system_prompt: str,
        history: list[dict[str, str]],
        user_message: str,
    ) -> list[dict[str, str]]:
        return (
            [{"role": "system", "content": system_prompt}]
            + history
            + [{"role": "user", "content": user_message}]
        )
