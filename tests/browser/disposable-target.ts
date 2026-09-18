export function assertDisposableTarget(health: unknown): void {
  if (
    !health ||
    typeof health !== "object" ||
    Reflect.get(health, "test_instance") !== true
  )
    throw new Error(
      "Refusing destructive fixtures: target is not an explicitly disposable Tempo test instance.",
    );
}
