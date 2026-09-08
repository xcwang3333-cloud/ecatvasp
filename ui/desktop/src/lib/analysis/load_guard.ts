export interface ElectronicAnalysisLoadToken {
  generation: number;
  projectRoot: string;
}

export class LatestElectronicAnalysisLoad {
  private generation = 0;

  begin(projectRoot: string): ElectronicAnalysisLoadToken {
    this.generation += 1;
    return { generation: this.generation, projectRoot };
  }

  isCurrent(token: ElectronicAnalysisLoadToken, projectRoot: string): boolean {
    return token.generation === this.generation && token.projectRoot === projectRoot;
  }

  invalidate(): void {
    this.generation += 1;
  }
}
