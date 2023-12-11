#!/bin/bash
#SBATCH --nodes=1
#SBATCH --cpus-per-task=CPUS
#SBATCH --ntasks=NTASKS
#SBATCH --time=5:00:00
#SBATCH --job-name=dpfn
#SBATCH --constraint=cpunode
#SBATCH --mail-type=FAIL
#SBATCH --mail-user=romijndersrob@gmail.com

source "/var/scratch/${USER}/projects/dpfn/scripts/preamble.sh"

echo `pwd`
echo "PYTHON: `which python`"
echo "WANDB: `which wandb`"
echo "SWEEP: $SWEEP, $SLURM_JOB_ID"

echo 'Starting'

export SWEEPID=$SWEEP

for i in {1..NTASKS}
do
   wandb agent "${WANDBUSERNAME}/dpfn-dpfn_experiments/$SWEEP" &
done

wait
