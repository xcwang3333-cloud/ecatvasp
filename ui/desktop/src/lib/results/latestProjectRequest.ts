export type ProjectRequestToken = Readonly<{
  projectRoot: string;
  sequence: number;
}>;

/**
 * Tracks the newest async request for a project-scoped view.
 *
 * Root equality alone is insufficient for A -> B -> A navigation because an
 * older A request can resolve after the newer A request. The monotonically
 * increasing sequence makes only the newest request authoritative.
 */
export class LatestProjectRequestGuard {
  private sequence = 0;

  begin(projectRoot: string): ProjectRequestToken {
    this.sequence += 1;
    return { projectRoot, sequence: this.sequence };
  }

  isCurrent(token: ProjectRequestToken, currentProjectRoot: string): boolean {
    return token.sequence === this.sequence && token.projectRoot === currentProjectRoot;
  }
}
