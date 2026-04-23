import tkinter as tk

class CounterApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Counter Test")
        self.counter = 0

        self.label = tk.Label(self, text=f"Count: {self.counter}", font=("Arial", 14))
        self.label.pack(pady=10)

        self.button = tk.Button(self, text="Increment", command=self.increment)
        self.button.pack(pady=5)

    def increment(self):
        self.counter += 1
        self.label.config(text=f"Count: {self.counter}")

if __name__ == "__main__":
    app = CounterApp()
    app.mainloop()
