import argparse
import asyncio
import logging
from .config import Settings
from .store import Store
from .http import Web
from .pipeline import Pipeline

async def run(args):
    settings = Settings()
    store = Store.connect(settings)
    store.indexes()
    web = Web(settings)
    pipeline = Pipeline(store, web, settings)
    try:
        if args.command in ('initial', 'seed'):
            pipeline.seed(args.cycle, args.page_size)
        if args.command in ('initial', 'worker'):
            if args.watch:
                await asyncio.gather(*(pipeline.worker(drain=False) for _ in range(settings.concurrency)))
            else:
                await pipeline.run()
        if args.command == 'status':
            print({state: store.db.tasks.count_documents({'status': state}) for state in ['pending', 'running', 'done', 'failed']})
    finally:
        await web.client.aclose()
        store.db.client.close()


def main():
    parser = argparse.ArgumentParser(description='Resumable Dutch vacancy discovery')
    parser.add_argument('command', choices=['initial', 'seed', 'worker', 'status'])
    parser.add_argument('--cycle', default='initial')
    parser.add_argument('--page-size', type=int, default=100)
    parser.add_argument('--watch', action='store_true')
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(parser.parse_args()))

if __name__ == '__main__':
    main()
