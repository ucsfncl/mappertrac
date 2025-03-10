#!/usr/bin/env python3
import argparse,parsl,os,sys,glob,shutil
from os.path import *
from mappertrac.subscripts import *

script_dir = abspath(os.path.dirname(os.path.realpath(__file__)))
cwd = abspath(os.getcwd())

def parse_args(args):

    parser = argparse.ArgumentParser()

    if not '--test' in sys.argv:
        parser.add_argument('inputs', nargs='+',
            help='Paths to BIDS subject folder(s).')

    parser.add_argument('--test', action='store_true',
        help='Test using the example subject.')

    workflow_group = parser.add_mutually_exclusive_group(required=True)

    workflow_group.add_argument('--freesurfer', '--s1_freesurfer', '-s1', action='store_true',
        help='Run step 1: dti preprocessing, freesurfer, and registration.')

    workflow_group.add_argument('--bedpostx', '--s2_bedpostx', '-s2', action='store_true',
        help='Run step 2: bedpostx.')

    workflow_group.add_argument('--probtrackx', '--s3_probtrackx', '-s3', action='store_true',
        help='Run step 3: probtrackx.')

    workflow_group.add_argument('--mrtrix', '--s2b_mrtrix', '-s2b', action='store_true',
        help='Run step 2b: alternative tractography pipeline to run after freesurfer.')

    parser.add_argument('--outputs', '-o', default='mappertrac_outputs/',
        help='Path to output directories.')

    parser.add_argument('--container', default=join(cwd, 'image.sif'),
        help='Path to Singularity container image.')

    parser.add_argument('--multi_container', default=join(cwd),
        help='Path to toolkit-specific containers.')

    parser.add_argument('--trac_sample_count', '--pbtx_sample_count', default=200,
        help='Number of tractography samples per voxel.')

    parser.add_argument('--conda_env', default='',
        help='Path to manually loaded conda environment for compute nodes.')

    parser.add_argument('--retries', default=0,
        help='Number of times to retry failed tasks.')

    parser.add_argument('--edgelist', default='reduced',
        help='Edge list for probtrackx: all (6642 edges) or reduced (930 edges).')

    scheduler_group = parser.add_mutually_exclusive_group()

    scheduler_group.add_argument('--slurm', action='store_true',
        help='Use the Slurm scheduler.')

    scheduler_group.add_argument('--cobalt', action='store_true',
        help='Use the Cobalt scheduler.')

    scheduler_group.add_argument('--grid_engine', action='store_true',
        help='Use the Grid Engine scheduler.')

    parser.add_argument('--nnodes', '-n', default=1,
        help='Scheduler: number of nodes (or cores for grid engine).')

    parser.add_argument('--bank', '-b', default='asccasc',
        help='Scheduler: bank to charge for jobs.')

    parser.add_argument('--partition', '-p', default='pbatch',
        help='Scheduler: partition or queue to assign jobs.')

    parser.add_argument('--walltime', '-t', default='11:59:00',
        help='Scheduler: walltime in format HH:MM:SS.')

    return parser.parse_args()

def main():

    args = parse_args(sys.argv[1:])

    if shutil.which('singularity') is None:
        raise Exception(f"Missing Singularity executable in PATH.\n\n" +
            f"Please ensure Singularity is installed: https://sylabs.io/guides/3.0/user-guide/installation.html")

    if not exists(args.multi_container):
        raise Exception(f"Missing container images at {abspath(args.containers)}\n\n" +
            f"Either specify another image with --container\n\n" +
            f"Or build the containers with the recipes at: {join(script_dir, 'data/container/')}")
    else:
        args.container=""

    if not exists(args.container) and not exists(args.multi_container):
        raise Exception(f"Missing container image at {abspath(args.container)}\n\n" +
            f"Either specify another image with --container\n\n" +
            f"Or build the container with the recipe at: {join(script_dir, 'data/container/recipe.def')}\n\n" +
            f"Or download the container at: https://osf.io/792up/download\n")

    if args.test:
        args.inputs = join(script_dir, 'data/example_inputs/sub-*/')

    if isinstance(args.inputs, str):
        args.inputs = glob(args.inputs)

    output_dir = abspath(normpath(args.outputs))
    smart_mkdir(output_dir)

    all_params = []

    # Copy reads to subject directory
    session_dirs = []
    subject_dirs = [] # subjects without sessions
    for input_dir in args.inputs:
        input_dir = abspath(input_dir)

        sessions = glob(join(input_dir, 'ses-*/'))
        if len(sessions) > 0:
            session_dirs += [normpath(_) for _ in sessions]
        else:
            subject_dirs += [normpath(input_dir)]

    base_params = {
        'container': abspath(args.container),
        'containers': abspath(args.multi_container),
        'script_dir': abspath(script_dir),
        'output_dir': output_dir,
        'edgelist': args.edgelist,
        'nnodes': int(args.nnodes),
        'trac_sample_count': int(args.trac_sample_count),
    }

    for input_dir in session_dirs:
        subject = basename(dirname(input_dir))
        session = basename(input_dir)
        subject_dir = join(output_dir, 'derivatives', subject, session)
        param = base_params.copy()
        param.update({
            'input_dir': input_dir,
            'work_dir': join(subject_dir, 'work_dir'),
            'ID': f'{subject}_{session}',
            'stdout': join(subject_dir, 'worker.stdout'),
        })
        all_params.append(param)

    for input_dir in subject_dirs:
        subject = basename(input_dir)
        subject_dir = join(output_dir, 'derivatives', subject)
        param = base_params.copy()
        param.update({
            'input_dir': input_dir,
            'work_dir': join(subject_dir, 'work_dir'),
            'ID': subject,
            'stdout': join(subject_dir, 'worker.stdout'),
        })
        all_params.append(param)

    if args.probtrackx:
        cores_per_worker = 1
        mem_per_worker = 0.1
        for params in all_params:
            work_dir = params['work_dir']
            bedpostxResults = join(work_dir,"bedpostx_b1000.bedpostX")
            subject_size = 0
            for dirpath, dirnames, filenames in os.walk(bedpostxResults):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if not os.path.islink(fp):
                        subject_size += os.path.getsize(fp)
            subject_size = 1.25 * subject_size * 1.0E-9
            if subject_size > mem_per_worker:
                mem_per_worker = subject_size
    else:
        cores_per_worker = int(os.cpu_count())
        mem_per_worker = None
    
    if args.conda_env:
        worker_init = (f"cd /wynton/home/mukherjee/shared/miniconda3\n" + 
                       f". bin/activate\n" +
                       f"export PATH=$PATH:/wynton/home/mukherjee/shared/miniconda3/bin\n" +
                       f"export PYTHONPATH=$PYTHONPATH:{os.getcwd()}\n" +
                       f"echo $( python3 -V )\n" +
                       f"echo $( conda -V )\n" +
                       f"conda activate {args.conda_env}")
    else:
        worker_init = ''

    if args.slurm:
        executor = parsl.executors.HighThroughputExecutor(
            label="worker",
            worker_debug=True,
            address=parsl.addresses.address_by_hostname(),
            cores_per_worker=cores_per_worker,
            mem_per_worker=mem_per_worker,
            provider=parsl.providers.SlurmProvider(
                args.partition,
                launcher=parsl.launchers.SrunLauncher(),
                nodes_per_block=int(args.nnodes),
                init_blocks=1,
                max_blocks=1,
                scheduler_options=f"#SBATCH --exclusive\n#SBATCH -A {args.bank}\n",
                worker_init=worker_init,
                walltime=args.walltime,
                move_files=False,
            ),
        )
    elif args.cobalt:
        executor = parsl.executors.HighThroughputExecutor(
            label="worker",
            worker_debug=True,
            address=parsl.addresses.address_by_hostname(),
            cores_per_worker=cores_per_worker,
            mem_per_worker=mem_per_worker,
            provider=parsl.providers.CobaltProvider(
                channel=parsl.channels.LocalChannel(),
                launcher=parsl.launchers.SimpleLauncher(),
                nodes_per_block=int(args.nnodes),
                init_blocks=1,
                max_blocks=1,
                scheduler_options=f"#SBATCH --exclusive\n#SBATCH -A {args.bank}\n",
                worker_init=worker_init,
                # worker_init='source /home/madduri/setup_cooley_env.sh',
                walltime=args.walltime,
                account=args.bank,
                queue=args.partition,
            ),
        )
    elif args.grid_engine:
        executor = parsl.executors.HighThroughputExecutor(
            label="worker",
            worker_debug=True,
            address=parsl.addresses.address_by_hostname(),
            max_workers=int(args.nnodes), # cap workers, or else defaults to infinity.
            mem_per_worker=mem_per_worker,
            provider=parsl.providers.GridEngineProvider(
                channel=parsl.channels.LocalChannel(),
                launcher=parsl.launchers.SingleNodeLauncher(),
                nodes_per_block=1,
                init_blocks=1,
                min_blocks=0,
                max_blocks=1,
                parallelism=1,
                maxcores=int(args.nnodes), # this passes to the qsub command in parsl to request consistent num of cores
                scheduler_options=f"#SBATCH --exclusive\n#SBATCH -A {args.bank}\n",
                worker_init=worker_init,
                walltime=args.walltime,
                queue=args.partition,
            ),
        )
    else:
        executor = parsl.executors.ThreadPoolExecutor(label="worker")

    config = parsl.config.Config(executors=[executor], retries=int(args.retries))
    parsl.clear()
    parsl.set_stream_logger()
    parsl.load(config)

    if args.freesurfer:
        results =  []
        for params in all_params:
            results.append(run_freesurfer(params))
        for r in results:
            r.result()

    elif args.bedpostx:
        results =  []
        for params in all_params:
            results.append(run_bedpostx(params))
        for r in results:
            r.result()

    elif args.probtrackx:
        results =  []
        for params in all_params:
            results.append(run_probtrackx(params))
        for r in results:
            r.result()

    elif args.mrtrix:
        results =  []
        for params in all_params:
            results.append(run_mrtrix(params))
        for r in results:
            r.result()

if __name__ == '__main__':
    main()

