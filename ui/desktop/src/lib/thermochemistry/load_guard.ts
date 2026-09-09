export interface ThermochemistryLoadToken {
  generation: number;
  projectRoot: string;
}

export class LatestThermochemistryLoad {
  private generation = 0;

  begin(projectRoot: string): ThermochemistryLoadToken {
    this.generation += 1;
    return { generation: this.generation, projectRoot };
  }

  isCurrent(token: ThermochemistryLoadToken, projectRoot: string): boolean {
    return token.generation === this.generation && token.projectRoot === projectRoot;
  }

  invalidate(): void {
    this.generation += 1;
  }
}
