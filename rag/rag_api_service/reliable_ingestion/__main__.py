import logging

from .runtime import run


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logging.getLogger('pika').setLevel(logging.WARNING)
    run()
