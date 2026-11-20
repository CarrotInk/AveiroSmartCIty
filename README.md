# Aveiro Smart City — Tech Week Hackathon 2026

Um projeto em contexto de hackathon para explorar como dados reais podem ajudar a melhorar a mobilidade em espaços públicos na cidade de Aveiro, Portugal.

## Objetivos do Projeto

- **Mobilidade**: Melhorar a mobilidade pedestre e por carro pela cidade.
- **Acessibilidade**: Tornar a informação mais acessível às pessoas para ser usada no dia a dia

## Estado Atual do Projeto:

O programa no seu estado atual analisa o dataset recolhido de uma semana e usa uma interface feita em tkinter para demonstrar esses dados de forma mais intuitiva.

## Conteúdo do Repositório

- `main.py` — Ficheiro principal, onde são analisados os dados do dataset e onde é feita a interface da app.
- `location_time.py` — Recolha de dados sobre a localização atual do utilizador.
- `road_match.py` — Funções que utilizam a localização do location_time.pv para estimar a rua mais próxima do utilizador
- `Data available.json` — Ficheiro complementar para ajudar a compreender os dados durante o desenvolvimento

## Preparar dados das estradas

O ficheiro `data/roadsegments_stats.csv` pode ser muito grande. Antes de usar a pesquisa
da estrada mais próxima pela primeira vez, execute `python road_match.py` na pasta do
projeto. O comando cria `data/roads_unique.csv`, com uma geometria por estrada; a
aplicação reutiliza e mantém em cache estes dados durante a execução.

## Acknowledgements

Feito como projeto da hackathon **Aveiro City Hackathon / Tech Week 2026** pela equipa:
**H.D.S.**:
- Henrique Romão
- Diogo Caseiro
- Simão Rocha
